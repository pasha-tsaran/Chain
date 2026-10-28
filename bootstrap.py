"""One-time model installer. No credentials or global Python changes."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(args):
    subprocess.run(args, check=True, cwd=ROOT)


def main():
    os.chdir(ROOT)
    python = ROOT / '.venv' / 'Scripts' / 'python.exe'
    if not python.exists():
        run([sys.executable, '-m', 'venv', str(ROOT / '.venv')])
    torch_ready = subprocess.run([str(python), '-c', 'import torch; assert torch.__version__.startswith("2.7.1")'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not torch_ready and sys.version_info[:2] == (3, 12) and os.name == 'nt':
        run([sys.executable, str(ROOT / 'download_torch.py')])
        run([str(python), '-m', 'pip', 'install', str(ROOT / 'data' / 'wheels' / 'torch-2.7.1+cu128-cp312-cp312-win_amd64.whl')])
    elif not torch_ready:
        run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--timeout', '120', 'torch==2.7.1', '--index-url', 'https://download.pytorch.org/whl/cu128'])
    run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--timeout', '120', '-r', str(ROOT / 'requirements-images.txt')])
    ollama = shutil.which('ollama')
    if not ollama:
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama' / 'ollama.exe'
        ollama = str(candidate) if candidate.is_file() else None
    if not ollama:
        raise RuntimeError('Установите Ollama: https://ollama.com/download/windows , затем повторите установку.')
    run([ollama, 'pull', 'qwen3:8b'])
    run([str(python), str(ROOT / 'download_sd.py')])
    run([str(python), str(ROOT / 'image_worker.py'), 'unused', '--download'])
    folder = ROOT / 'models'
    folder.mkdir(exist_ok=True)
    (folder / 'image-ready.json').write_text(json.dumps({'model': 'stabilityai/sd-turbo', 'installed': True}), encoding='utf-8')
    print('\nГотово. Запустите «Запустить Редакцию.cmd».')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f'Установка не завершена: {exc}\nЗапустите установку повторно: уже загруженные файлы будут использованы.')
        sys.exit(1)
