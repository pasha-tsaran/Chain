"""Loopback-only, dependency-free web application."""
import argparse
import html
import io
import json
import mimetypes
import re
import subprocess
import sys
import threading
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import storage
import workflow
from providers import Ollama, ProviderError, request_json
from rendering import article_markdown, detector_text, render

BUSY = threading.Lock()
POST_LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=1)


def launch(article_id, operation, *args):
    if not BUSY.acquire(blocking=False):
        raise ValueError('Сейчас выполняется другая операция. Дождитесь её завершения.')
    config = storage.settings()
    def work():
        try:
            workflow.guarded(article_id, config, operation, *args)
        finally:
            BUSY.release()
    POOL.submit(work)


def public_article(article):
    result = dict(article)
    result['html'] = render('\n\n'.join(b['markdown'] for b in article['blocks']))
    result['markdown'] = article_markdown(article)
    result['detector_text'] = detector_text(article)
    result['hash'] = workflow.digest(article)
    result['busy'] = BUSY.locked()
    return result


def local_url(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Адрес локальной модели должен иметь вид http://127.0.0.1:порт.')
    return value.rstrip('/')


def update_settings(data):
    previous = storage.settings()
    for key in storage.DEFAULTS:
        if key in data:
            previous[key] = data[key]
    previous['ollama_url'] = local_url(str(previous['ollama_url']))
    previous['sd_url'] = local_url(str(previous['sd_url']))
    if previous['detector'] not in ('manual', 'browser', 'zerogpt') or previous['image_provider'] not in ('local', 'webui'):
        raise ValueError('Неизвестный сервис.')
    if not re.fullmatch(r'[a-zA-Z0-9._:/-]{1,150}', str(previous['model'])):
        raise ValueError('Некорректное имя модели.')
    previous['max_revisions'] = int(previous['max_revisions'])
    if not 1 <= previous['max_revisions'] <= 5:
        raise ValueError('Лимит доработок: от 1 до 5.')
    for key in ('zerogpt_key', 'zerogpt_token'):
        if not isinstance(previous[key], str) or len(previous[key]) > 6000:
            raise ValueError('Некорректный ключ сервиса.')
    storage.save_settings(previous)


def status():
    config = storage.settings()
    safe = {k: v for k, v in config.items() if k not in ('zerogpt_token', 'zerogpt_key')}
    safe['has_detector_credentials'] = bool(config['zerogpt_token'] and config['zerogpt_key'])
    models, error = [], None
    try:
        result = request_json(config['ollama_url'] + '/api/tags', timeout=3)
        models = [m['name'] for m in result.get('models', [])]
    except ProviderError as exc:
        error = str(exc)
    ready_file = storage.ROOT / 'models' / 'image-ready.json'
    return {'settings': safe, 'models': models, 'model_ready': config['model'] in models,
            'images_ready': ready_file.exists() if config['image_provider'] == 'local' else None,
            'ollama_error': error, 'busy': BUSY.locked()}


def image_html(article, index, embedded=False):
    if index >= len(article['images']):
        return ''
    image = article['images'][index]
    return f'<figure><img src="images/{html.escape(image["filename"])}" alt="{html.escape(image["caption"])}"><figcaption>{html.escape(image["caption"])}</figcaption></figure>'


def export(article):
    md = article_markdown(article)
    parts = [f'<h1>{html.escape(article["title"])}</h1>', image_html(article, 0)]
    for i, block in enumerate(article['blocks']):
        parts.append(render(block['markdown']))
        if i == len(article['blocks']) // 2:
            parts.append(image_html(article, 1))
    css = 'body{font:19px/1.75 Georgia,serif;max-width:780px;margin:50px auto;padding:0 24px;color:#252521}h1{font-size:40px;line-height:1.2}h2{line-height:1.3;margin-top:36px}img{max-width:100%;height:auto}figure{margin:30px 0}figcaption{font:13px/1.5 Arial;color:#666}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:10px}blockquote{border-left:3px solid #999;padding-left:20px}.table-scroll{overflow-x:auto}'
    document = '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(article['title']) + '</title><style>' + css + '</style><body>' + '\n'.join(parts) + '</body></html>'
    if article['images']:
        # Markdown export also carries actual image references.
        blocks = article['blocks']
        content = []
        for i, block in enumerate(blocks):
            if i == 0:
                content.append(f'![Иллюстрация, создано ИИ](images/{article["images"][0]["filename"]})')
            content.append(block['markdown'])
            if i == len(blocks)//2 and len(article['images']) > 1:
                content.append(f'![Иллюстрация, создано ИИ](images/{article["images"][1]["filename"]})')
        md = '# ' + article['title'] + '\n\n' + '\n\n'.join(content)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('article.md', md)
        archive.writestr('article.html', document)
        archive.writestr('detector-text.txt', detector_text(article))
        archive.writestr('report.json', json.dumps(article, ensure_ascii=False, indent=2))
        archive.writestr('ПРОЧИТАЙТЕ.txt', 'Откройте article.html для чтения и переноса оформления в редактор Дзена.\nКартинки находятся в images. Проверьте факты и отображение таблиц в редакторе Дзена.\nСтатус проверки и версии находятся в report.json. Непроверенный текст тоже можно скачать.\n')
        for image in article['images']:
            path = storage.DATA / 'articles' / article['id'] / image['filename']
            if path.is_file():
                archive.write(path, 'images/' + image['filename'])
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def send(self, body, code=200, content_type='application/json; charset=utf-8', filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, post=False):
        valid_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in valid_hosts:
            self.send({'error': 'Недопустимый Host.'}, 403)
            return False
        if post and (self.headers.get('X-Studio-Request') != '1' or self.headers.get('Origin', 'http://' + self.headers['Host']) != 'http://' + self.headers['Host']):
            self.send({'error': 'Разрешены только запросы из локального приложения.'}, 403)
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == '/api/status':
                return self.send(status())
            if path == '/api/articles':
                return self.send([{'id': a['id'], 'title': a['title'], 'state': a['state'], 'created': a['created'], 'version': a['version']} for a in storage.all_articles()])
            match = re.fullmatch(r'/api/articles/([a-f0-9]{32})(/export)?', path)
            if match:
                article = storage.get(match[1])
                if match[2]:
                    return self.send(export(article), content_type='application/zip', filename=f'article-{article["id"][:8]}.zip')
                return self.send(public_article(article))
            match = re.fullmatch(r'/media/([a-f0-9]{32})/(image-[12]\.png)', path)
            if match:
                image = storage.DATA / 'articles' / match[1] / match[2]
                if image.is_file():
                    return self.send(image.read_bytes(), content_type='image/png')
            static = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
            if path in static:
                file = storage.ROOT / 'static' / static[path]
                return self.send(file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] + '; charset=utf-8')
            self.send({'error': 'Не найдено.'}, 404)
        except KeyError:
            self.send({'error': 'Статья не найдена.'}, 404)
        except (ConnectionError, BrokenPipeError):
            pass

    def do_POST(self):
        with POST_LOCK:
            self.handle_post()

    def handle_post(self):
        if not self.allowed(post=True):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 150000:
                return self.send({'error': 'Слишком большой запрос.'}, 413)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Требуется JSON-объект.')
            if BUSY.locked():
                return self.send({'error': 'Дождитесь завершения текущей операции.'}, 409)
            path = urllib.parse.urlparse(self.path).path
            if path == '/api/settings':
                update_settings(body)
                return self.send({'ok': True})
            if path == '/api/articles':
                config = status()
                if not config['model_ready']:
                    raise ValueError('Выбранная модель не установлена. Выполните «Установить модели.cmd» или выберите установленную модель в настройках.')
                if config['settings']['image_provider'] == 'local' and not config['images_ready']:
                    raise ValueError('Модель картинок ещё не установлена. Выполните «Установить модели.cmd» и дождитесь завершения.')
                article = workflow.new_article(body)
                launch(article['id'], workflow.generate)
                return self.send({'id': article['id']}, 202)
            match = re.fullmatch(r'/api/articles/([a-f0-9]{32})/(report|edit|images|recheck|retry)', path)
            if not match:
                return self.send({'error': 'Не найдено.'}, 404)
            article = storage.get(match[1])
            action = match[2]
            article['error'] = None
            if action == 'report':
                report = workflow.attach_report(article, {'score': body.get('score'), 'fragments': body.get('fragments', []),
                                                          'provider': 'ZeroGPT, результат введён пользователем'}, body.get('hash'))
                article['state'] = 'running'
                storage.save(article)
                launch(article['id'], lambda a, s: workflow.detection_loop(a, s, report))
            elif action == 'edit':
                if body.get('hash') != workflow.digest(article):
                    raise ValueError('Версия изменилась. Откройте редактор заново.')
                title = body.get('title', '')
                markdown = body.get('markdown', '')
                title, blocks = workflow.validate_draft({'title': title, 'blocks': re.split(r'\n\s*\n', markdown.strip())})
                article['title'], article['blocks'] = title, blocks
                workflow.version(article, 'Ручная редактура')
                article['state'] = 'awaiting_check'
                article['review'] = {'notes': ['После ручного изменения текста проверьте факты и соответствие иллюстраций заново.']}
                storage.save(article)
            elif action == 'images':
                if not article.get('plan') or not article['blocks']:
                    raise ValueError('Сначала создайте текст и план.')
                article['state'] = 'running'
                storage.save(article)
                def images(a, s):
                    workflow.event(a, 3, 'Повторно создаём иллюстрации.')
                    workflow.generate_images(a, s, Ollama(s))
                    a['state'] = 'checked' if a['check'] and a['check']['score'] <= 10 else 'awaiting_check'
                    workflow.event(a, 4, 'Иллюстрации сохранены.')
                launch(article['id'], images)
            elif action == 'retry':
                if article['blocks']:
                    raise ValueError('Текст уже создан: используйте проверку или повторную генерацию картинок.')
                article['state'] = 'running'
                storage.save(article)
                launch(article['id'], workflow.generate)
            else:
                if not article['blocks']:
                    raise ValueError('Нет текста для проверки.')
                article['state'] = 'running'
                storage.save(article)
                launch(article['id'], workflow.detection_loop)
            self.send({'ok': True}, 202)
        except KeyError:
            self.send({'error': 'Статья не найдена.'}, 404)
        except (ValueError, TypeError, OverflowError, ProviderError) as exc:
            self.send({'error': str(exc) or 'Некорректный запрос.'}, 400)
        except Exception:
            import traceback
            traceback.print_exc()
            self.send({'error': 'Внутренняя ошибка. Подробности в server.log.'}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    # Mark interrupted jobs honestly; drafts and revisions remain intact.
    for article in storage.all_articles():
        if article['state'] == 'running':
            article['state'] = 'error'
            article['error'] = 'Предыдущий запуск прерван. Черновик сохранён.'
            storage.save(article)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Редакция: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
