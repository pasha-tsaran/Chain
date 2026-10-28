import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def alive():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


def main():
    os.chdir(ROOT)
    try:
        urllib.request.urlopen('http://127.0.0.1:11434/api/version', timeout=2).close()
    except OSError:
        ollama = shutil.which('ollama')
        if ollama:
            subprocess.Popen([ollama, 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if not alive():
        python = ROOT / '.venv' / 'Scripts' / 'python.exe'
        if not python.exists():
            python = Path(sys.executable)
        with (ROOT / 'server.log').open('a', encoding='utf-8') as log:
            subprocess.Popen([str(python), str(ROOT / 'server.py')], cwd=ROOT, stdout=log, stderr=log,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(30):
            if alive():
                break
            time.sleep(0.5)
        else:
            print('Не удалось запустить приложение. Подробности в server.log.')
            return 1
    webbrowser.open('http://127.0.0.1:8765')
    return 0


if __name__ == '__main__':
    sys.exit(main())
