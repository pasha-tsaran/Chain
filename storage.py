import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
LOCK = threading.RLock()
DEFAULTS = {'ollama_url': 'http://127.0.0.1:11434', 'model': 'qwen3:8b',
            'detector': 'manual', 'image_provider': 'local', 'sd_url': 'http://127.0.0.1:7860',
            'max_revisions': 3, 'zerogpt_token': '', 'zerogpt_key': ''}


@contextmanager
def connect():
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DATA / 'studio.sqlite3', timeout=30)
    try:
        con.execute('CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        with con:
            yield con
    finally:
        con.close()


def save(article):
    with LOCK, connect() as con:
        con.execute('INSERT OR REPLACE INTO articles VALUES (?, ?)', (article['id'], json.dumps(article, ensure_ascii=False)))


def get(article_id):
    with connect() as con:
        row = con.execute('SELECT payload FROM articles WHERE id=?', (article_id,)).fetchone()
    if not row:
        raise KeyError('Статья не найдена.')
    return json.loads(row[0])


def all_articles():
    with connect() as con:
        rows = con.execute('SELECT payload FROM articles ORDER BY rowid DESC').fetchall()
    return [json.loads(row[0]) for row in rows]


def settings():
    path = DATA / 'settings.json'
    value = DEFAULTS.copy()
    if path.exists():
        value.update(json.loads(path.read_text(encoding='utf-8')))
    value['zerogpt_token'] = os.environ.get('ZEROGPT_TOKEN', value['zerogpt_token'])
    value['zerogpt_key'] = os.environ.get('ZEROGPT_API_KEY', value['zerogpt_key'])
    return value


def save_settings(value):
    DATA.mkdir(exist_ok=True)
    target = DATA / 'settings.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)
