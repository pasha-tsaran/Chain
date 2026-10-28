"""Local inference and documented ZeroGPT Business API adapter."""
import json
import math
import urllib.error
import urllib.request


class ProviderError(Exception):
    pass


def request_json(url, payload=None, headers=None, timeout=900):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Never expose bodies that may echo credentials.
        raise ProviderError(f'Сервис ответил HTTP {exc.code}. Проверьте настройки и доступность.') from None
    except (OSError, ValueError) as exc:
        raise ProviderError(f'Не удалось связаться с сервисом ({type(exc).__name__}). Проверьте, запущена ли модель.') from None


class Ollama:
    def __init__(self, settings):
        self.base = settings['ollama_url'].rstrip('/')
        self.model = settings['model']

    def call(self, system, data, temperature=0.45, reason=False):
        payload = {'model': self.model, 'stream': False, 'format': 'json',
                   'keep_alive': '10m', 'options': {'temperature': temperature, 'num_ctx': 8192, 'num_predict': 10000 if reason else 6000},
                   'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]}
        if self.model.startswith(('qwen3', 'deepseek-r1', 'gpt-oss')):
            payload['think'] = reason
        for attempt in range(2):
            response = request_json(self.base + '/api/chat', payload)
            content = response.get('message', {}).get('content', '').strip()
            if response.get('done_reason') == 'length':
                raise ProviderError('Модель исчерпала лимит ответа. Уменьшите объём статьи.')
            try:
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
            except (ValueError, TypeError):
                payload['messages'].append({'role': 'assistant', 'content': content[:24000]})
                payload['messages'].append({'role': 'user', 'content': 'Верни корректный JSON-объект по указанной схеме. Без пояснений.'})
        raise ProviderError('Модель дважды вернула некорректный JSON. Попробуйте другую модель.')

    def unload(self):
        request_json(self.base + '/api/generate', {'model': self.model, 'keep_alive': 0})


def validate_report(score, fragments):
    if isinstance(score, bool):
        raise ValueError('Укажите числовой процент от 0 до 100.')
    score = float(score)
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError('Процент должен быть от 0 до 100.')
    if not isinstance(fragments, list) or len(fragments) > 150 or any(not isinstance(f, str) or not f.strip() for f in fragments):
        raise ValueError('Фрагменты должны быть непустыми строками.')
    return score, list(dict.fromkeys(fragments))


def detect(article_text, settings):
    token, key = settings.get('zerogpt_token', ''), settings.get('zerogpt_key', '')
    if not token or not key:
        raise ProviderError('Для автоматической проверки нужны JWT и API Key ZeroGPT Business. Бесплатный режим — перенос результата с сайта.')
    result = request_json('https://api.zerogpt.com/api/detect/detectText', {'input_text': article_text},
                          {'Authorization': 'Bearer ' + token, 'ApiKey': key}, timeout=90)
    if result.get('success') is not True or not isinstance(result.get('data'), dict):
        raise ProviderError('ZeroGPT не вернул успешный результат. Проверьте баланс и срок действия JWT.')
    data = result['data']
    if 'fakePercentage' not in data:
        raise ProviderError('В ответе ZeroGPT нет процента. Проверка не засчитана.')
    try:
        score, fragments = validate_report(data['fakePercentage'], data.get('h', []))
    except (ValueError, TypeError):
        raise ProviderError('Неподдерживаемый формат отчёта ZeroGPT. Проверка не засчитана.') from None
    return {'score': score, 'fragments': fragments, 'provider': 'ZeroGPT Business API'}
