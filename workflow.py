import base64
import difflib
import hashlib
import json
import random
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import prompts
import storage
from providers import Ollama, ProviderError, detect, request_json, validate_report
from rendering import article_markdown, detector_text, plain_with_map

FORMATS = {
    'auto': ['Практическая статья: короткое вступление, действия списком, умеренный жирный акцент.',
             'Разбор ситуации: сцена, объяснение, краткая курсивная оговорка и вывод.',
             'Сравнение вариантов: краткое вступление, таблица с содержательными критериями, пояснения.',
             'Ответ на частую проблему: подзаголовки, связные абзацы, один жирный акцент.'],
    'guide': ['Практическая статья с последовательными шагами в нумерованном списке.'],
    'essay': ['Связное авторское эссе: абзацы разной длины, курсивная оговорка, минимум списков.'],
    'comparison': ['Содержательное сравнение с одной Markdown-таблицей и пояснениями.'],
}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(article):
    return hashlib.sha256(detector_text(article).encode('utf-8')).hexdigest()


def event(article, stage, message):
    article['stage'] = stage
    article['events'].append({'at': now(), 'stage': stage, 'message': message})
    article['updated'] = now()
    storage.save(article)


def version(article, reason):
    article['version'] += 1
    article['check'] = None
    article['versions'].append({'version': article['version'], 'at': now(), 'reason': reason,
                                'title': article['title'], 'blocks': json.loads(json.dumps(article['blocks'])),
                                'hash': digest(article)})
    storage.save(article)


def new_article(brief):
    topic = str(brief.get('topic', '')).strip()
    if not 4 <= len(topic) <= 500:
        raise ValueError('Тема должна содержать от 4 до 500 символов.')
    length = int(brief.get('length', 5000))
    count = int(brief.get('image_count', 2))
    fmt = brief.get('format', 'auto')
    if length not in (3000, 5000, 7500) or count not in (1, 2) or fmt not in FORMATS:
        raise ValueError('Недопустимый объём, формат или количество картинок.')
    for field, limit in [('audience', 500), ('instructions', 4000), ('materials', 16000)]:
        if not isinstance(brief.get(field, ''), str) or len(brief.get(field, '')) > limit:
            raise ValueError(f'Слишком длинное поле {field}.')
    article = {'id': uuid.uuid4().hex, 'title': topic, 'brief': {**brief, 'topic': topic, 'length': length, 'image_count': count, 'format': fmt},
               'created': now(), 'updated': now(), 'state': 'running', 'stage': 1,
               'blocks': [], 'images': [], 'events': [], 'versions': [], 'reports': [], 'version': 0,
               'revision_count': 0, 'check': None, 'review': {}, 'error': None}
    storage.save(article)
    return article


def validate_draft(value):
    if not isinstance(value.get('title'), str) or not 4 <= len(value['title']) <= 250:
        raise ProviderError('Модель не вернула корректный заголовок.')
    blocks = value.get('blocks')
    if not isinstance(blocks, list) or not 3 <= len(blocks) <= 100 or any(not isinstance(b, str) or not b.strip() for b in blocks):
        raise ProviderError('Модель не вернула структуру статьи. Попробуйте другую модель.')
    if not 800 <= sum(len(b) for b in blocks) <= 35000:
        raise ProviderError('Модель вернула слишком короткую или слишком длинную статью.')
    return value['title'].strip(), [{'id': f'b{i + 1}', 'markdown': b.strip()} for i, b in enumerate(blocks)]


def _rewrite_once(llm, original, context, reason):
    response = llm.call(prompts.REWRITE, {'fragment': original, 'context': context[:3500], 'reason': reason}, temperature=0.5)
    replacement = response.get('replacement', '')
    if not isinstance(replacement, str) or not replacement.strip() or len(replacement) > max(90, len(original) * 1.6):
        raise ProviderError('Редактор вернул некорректную замену. Исходный текст сохранён.')
    outside = context.replace(original, '', 1)
    if difflib.SequenceMatcher(None, replacement, outside, autojunk=False).find_longest_match().size >= 60:
        raise ProviderError('Редактор повторил соседний текст. Замена отклонена.')
    # Preserve numeric facts; names and meaning are additionally constrained by the prompt.
    if re.findall(r'\d+(?:[.,]\d+)?', original) != re.findall(r'\d+(?:[.,]\d+)?', replacement):
        raise ProviderError('Редактор изменил числа. Замена отклонена, исходный текст сохранён.')
    for token in ('**', '*', '`'):
        if original.count(token) % 2 != replacement.count(token) % 2:
            raise ProviderError('Редактор нарушил оформление. Замена отклонена.')
    return replacement.strip()


def rewrite_raw(llm, original, context, reason):
    for attempt in range(2):
        try:
            return _rewrite_once(llm, original, context, reason)
        except ProviderError as exc:
            if attempt:
                raise
            reason += ' Предыдущая замена отклонена: ' + str(exc) + ' Верни только короткую замену исходного fragment без соседних предложений.'


def editorial_review(article, llm):
    result = llm.call(prompts.REVIEW, {'title': article['title'], 'blocks': article['blocks'],
                                     'brief': article['brief']}, temperature=0.15, reason=True)
    notes = result.get('notes', [])
    article['review']['notes'] = [str(n)[:1000] for n in notes[:12]] if isinstance(notes, list) else []
    patches = result.get('patches', [])
    changed = []
    if isinstance(patches, list):
        for patch in patches[:10]:
            if not isinstance(patch, dict):
                continue
            block = next((b for b in article['blocks'] if b['id'] == patch.get('block_id')), None)
            quote = patch.get('quote', '')
            if not block or not isinstance(quote, str) or len(quote) < 8 or block['markdown'].count(quote) != 1:
                continue
            try:
                replacement = rewrite_raw(llm, quote, block['markdown'], str(patch.get('reason', 'Уточнить формулировку.')))
                if replacement != quote:
                    block['markdown'] = block['markdown'].replace(quote, replacement, 1)
                    changed.append({'before': quote, 'after': replacement, 'block_id': block['id']})
            except ProviderError as exc:
                article['review']['notes'].append(str(exc))
    if changed:
        article['review']['edits'] = changed
        version(article, 'Точечная литературная редактура')
    size = len(detector_text(article))
    target = article['brief']['length']
    article['review']['characters'] = size
    article['review']['warnings'] = []
    if not target * 0.7 <= size <= target * 1.35:
        article['review']['warnings'].append(f'Объём {size} символов отличается от задания {target}.')
    if not article['brief'].get('materials', '').strip():
        article['review']['warnings'].append('Источники не предоставлены. Проверьте фактические утверждения перед публикацией.')
    supplied = ' '.join(str(article['brief'].get(k, '')) for k in ('topic', 'materials', 'instructions'))
    supported_numbers = set(re.findall(r'\d+(?:[.,]\d+)?', supplied))
    generated_numbers = set(re.findall(r'\d+(?:[.,]\d+)?', detector_text(article)))
    unsupported = generated_numbers - supported_numbers
    if unsupported:
        article['review']['warnings'].append('В исходных материалах нет этих чисел: ' + ', '.join(sorted(unsupported)) + '. Уточните их источник или отредактируйте утверждения.')
    storage.save(article)


def generate_images(article, settings, llm):
    directory = storage.DATA / 'articles' / article['id']
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    plans = article['plan'].get('image_prompts', [])
    if not isinstance(plans, list) or len(plans) < article['brief']['image_count']:
        raise ProviderError('Аналитик не подготовил описания иллюстраций. Повторите создание статьи.')
    if any(not isinstance(p, str) or re.search(r'[А-Яа-яЁё]', p) for p in plans[:article['brief']['image_count']]):
        result = llm.call(prompts.IMAGE_BRIEF, {'title': article['title'], 'scenes': plans,
                                              'count': article['brief']['image_count']}, temperature=0.3)
        plans = result.get('prompts', [])
        if not isinstance(plans, list) or len(plans) != article['brief']['image_count'] or any(not isinstance(p, str) or re.search(r'[А-Яа-яЁё]', p) for p in plans):
            raise ProviderError('Не удалось подготовить английские описания сцен для модели изображений. Текст сохранён.')
        article['plan']['image_prompts'] = plans
        storage.save(article)
    for i in range(article['brief']['image_count']):
        prompt = plans[i]
        if not isinstance(prompt, str) or not 15 <= len(prompt) <= 2500:
            raise ProviderError('Некорректное описание иллюстрации.')
        manifest.append({'prompt': prompt, 'path': str(directory / f'image-{i+1}.png'), 'seed': random.randrange(2**31)})
    if settings['image_provider'] == 'local':
        # Text and diffusion models share an 8 GB GPU: release the text model first.
        llm.unload()
        (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        with (directory / 'images.log').open('w', encoding='utf-8') as logfile:
            try:
                result = subprocess.run([sys.executable, str(storage.ROOT / 'image_worker.py'), str(directory / 'manifest.json')],
                                        stdout=logfile, stderr=subprocess.STDOUT, timeout=1200,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=storage.ROOT)
            except subprocess.TimeoutExpired:
                raise ProviderError('Генерация картинок превысила 20 минут. Текст сохранён.') from None
        if result.returncode:
            raise ProviderError('Локальная модель картинок не запустилась. Выполните «Установить модели.cmd». Подробности в data/articles/<id>/images.log. Текст сохранён.')
    else:
        for item in manifest:
            response = request_json(settings['sd_url'].rstrip('/') + '/sdapi/v1/txt2img',
                                    {'prompt': item['prompt'], 'negative_prompt': 'text, watermark, logo, blurry',
                                     'width': 768, 'height': 512, 'steps': 20, 'seed': item['seed']}, timeout=600)
            images = response.get('images', [])
            if not images:
                raise ProviderError('Сервер Stable Diffusion не вернул картинку.')
            Path(item['path']).write_bytes(base64.b64decode(images[0].split(',')[-1], validate=True))
    article['images'] = [{'filename': Path(x['path']).name, 'prompt': x['prompt'], 'seed': x['seed'],
                          'caption': 'Иллюстрация, созданная ИИ.'} for x in manifest]
    storage.save(article)


def map_fragments(article, fragments):
    """Require unambiguous exact fragments; never fall back to whole-document editing."""
    mapped, errors = [], []
    for fragment in fragments:
        candidates = []
        for block in [{'id': 'title', 'markdown': article['title']}, *article['blocks']]:
            plain, offsets = plain_with_map(block['markdown'])
            if plain.count(fragment) == 1:
                start = plain.index(fragment)
                candidates.append((block['id'], offsets[start], offsets[start + len(fragment) - 1] + 1))
            elif plain.count(fragment) > 1:
                candidates.extend([None, None])
        if len(candidates) != 1 or candidates[0] is None:
            errors.append('Не найден однозначный фрагмент: ' + fragment[:100])
        else:
            mapped.append(candidates[0])
    mapped.sort()
    for previous, current in zip(mapped, mapped[1:]):
        if previous[0] == current[0] and current[1] < previous[2]:
            errors.append('Выделенные фрагменты пересекаются. Передайте одну объединённую цитату.')
    if errors:
        raise ValueError('\n'.join(errors))
    return mapped


def patch_detected(article, fragments, settings):
    spans = map_fragments(article, fragments)
    if not spans:
        raise ValueError('Нужны точные фрагменты, выделенные детектором.')
    llm = Ollama(settings)
    edits = []
    # Stage all replacements first: any failure leaves the entire version unchanged.
    for block_id, start, end in spans:
        block = next(b for b in [{'id': 'title', 'markdown': article['title']}, *article['blocks']] if b['id'] == block_id)
        original = block['markdown'][start:end]
        replacement = rewrite_raw(llm, original, block['markdown'], 'Детектор выделил этот фрагмент. Уточни формулировку, убери речевые штампы, сохрани смысл.')
        edits.append({'block_id': block_id, 'start': start, 'end': end, 'before': original, 'after': replacement})
    if all(e['before'] == e['after'] for e in edits):
        article['state'] = 'needs_review'
        event(article, 4, 'Редактор не изменил выделенные фрагменты. Требуется ручная редактура.')
        return False
    updated = {'title': article['title'], **{b['id']: b['markdown'] for b in article['blocks']}}
    for edit in sorted(edits, key=lambda e: (e['block_id'], e['start']), reverse=True):
        text = updated[edit['block_id']]
        updated[edit['block_id']] = text[:edit['start']] + edit['after'] + text[edit['end']:]
    if not 4 <= len(updated['title']) <= 250 or sum(len(v) for k, v in updated.items() if k != 'title') > 35000:
        raise ProviderError('Замены превышают допустимый объём. Исходная версия сохранена.')
    article['title'] = updated['title']
    for block in article['blocks']:
        block['markdown'] = updated[block['id']]
    article['revision_count'] += 1
    article.setdefault('detector_edits', []).append(edits)
    version(article, 'Правка только выделенных детектором фрагментов')
    return True


def attach_report(article, report, expected_hash):
    if expected_hash != digest(article):
        raise ValueError('Текст изменился после копирования. Проверьте текущую версию заново.')
    score, fragments = validate_report(report['score'], report.get('fragments', []))
    # Reject bad highlights before storing a report that could trigger mutation.
    if score > 10 and fragments:
        map_fragments(article, fragments)
    record = {**report, 'score': score, 'fragments': fragments, 'hash': expected_hash,
              'version': article['version'], 'at': now()}
    article['check'] = record
    article['reports'].append(record)
    storage.save(article)
    return record


def detection_loop(article, settings, initial=None):
    report = initial
    while True:
        if report is None:
            if settings['detector'] == 'manual':
                article['state'] = 'awaiting_check'
                event(article, 4, 'Статья ждёт проверки на бесплатном сайте. Скопируйте текст и перенесите процент и выделенные фрагменты.')
                return
            if settings['detector'] == 'browser':
                event(article, 4, 'Проверяем текущую версию через бесплатную форму ZeroGPT в локальном браузере.')
                from browser_detector import detect_browser
                try:
                    result = detect_browser(detector_text(article), storage.DATA / 'articles' / article['id'])
                except ProviderError as exc:
                    article['state'] = 'awaiting_check'
                    event(article, 4, str(exc))
                    return
            else:
                event(article, 4, 'Проверяем текущую версию через ZeroGPT Business API.')
                result = detect(detector_text(article), settings)
            report = attach_report(article, result, digest(article))
        if report['score'] <= 10:
            article['state'] = 'checked'
            event(article, 4, f'Оценка детектора {report["score"]:g}%: порог 10% достигнут для версии {article["version"]}. Это оценка сервиса, а не доказательство авторства.')
            return
        if article['revision_count'] >= settings['max_revisions']:
            article['state'] = 'needs_review'
            event(article, 4, 'Лимит доработок достигнут, порог не пройден. Текст и все версии сохранены для ручной редакции.')
            return
        if not report['fragments']:
            article['state'] = 'needs_fragments'
            event(article, 4, 'Оценка выше 10%. Добавьте точные цитаты, выделенные детектором, чтобы изменить только их.')
            return
        event(article, 4, f'Точечно редактируем {len(report["fragments"])} фрагм., проход {article["revision_count"] + 1}.')
        if not patch_detected(article, report['fragments'], settings):
            return
        report = None


def generate(article, settings):
    """Run the four-stage Chain; pass each stage's result to the next."""
    llm = Ollama(settings)
    # ЭТАП 1. Анализ задачи: brief -> структурированный plan.
    event(article, 1, 'Анализируем тему, аудиторию, ракурс и ограничения фактов.')
    plan = llm.call(prompts.ANALYZE, article['brief'], temperature=0.45)
    if not isinstance(plan.get('outline'), list) or not plan.get('angle'):
        raise ProviderError('Аналитик не вернул план статьи.')
    article['plan'] = plan
    # ЭТАП 2. Подбор инструментов и формата по настройкам приложения.
    previous = next((x.get('format_instruction') for x in storage.all_articles() if x['id'] != article['id'] and x.get('format_instruction')), None)
    choices = FORMATS[article['brief']['format']]
    article['format_instruction'] = random.choice([c for c in choices if c != previous] or choices)
    article['tools'] = {'text': settings['model'], 'images': 'SD-Turbo local' if settings['image_provider'] == 'local' else 'Stable Diffusion WebUI',
                        'detector': {'manual': 'Бесплатная проверка, перенос отчёта вручную', 'browser': 'Автоматическая бесплатная форма ZeroGPT', 'zerogpt': 'ZeroGPT Business API'}[settings['detector']]}
    event(article, 2, f'Выбраны {article["tools"]["text"]}, {article["tools"]["images"]}; формат: {article["format_instruction"]}')
    # ЭТАП 3. Реализация: brief + plan + format -> статья и картинки.
    event(article, 3, 'Пишем статью по плану.')
    draft = llm.call(prompts.WRITE, {'brief': article['brief'], 'plan': plan, 'format': article['format_instruction']}, reason=True)
    article['title'], article['blocks'] = validate_draft(draft)
    version(article, 'Первый черновик')
    event(article, 3, 'Внутренняя редактура: проверяем текст, факты и речевые штампы.')
    editorial_review(article, llm)
    event(article, 3, 'Создаём тематические иллюстрации. Текстовая модель освобождает видеопамять.')
    generate_images(article, settings, llm)
    # ЭТАП 4. Проверка / ревью: отчёт -> точечные правки -> повторная проверка.
    detection_loop(article, settings)


def guarded(article_id, settings, operation, *args):
    article = storage.get(article_id)
    try:
        operation(article, settings, *args)
    except Exception as exc:
        article['state'] = 'error'
        article['error'] = str(exc)[:1500] if isinstance(exc, (ValueError, ProviderError)) else f'Ошибка {type(exc).__name__}. Черновик сохранён. Подробности в server.log.'
        import traceback
        traceback.print_exc()
        event(article, article.get('stage', 1), article['error'])
    finally:
        storage.save(article)
