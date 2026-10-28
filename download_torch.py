"""Resumable ranged download from the official PyTorch wheel index."""
import concurrent.futures
import hashlib
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    name = 'torch-2.7.1+cu128-cp312-cp312-win_amd64.whl'
    index = urllib.request.urlopen('https://download.pytorch.org/whl/cu128/torch/', timeout=60).read().decode()
    link = next(link for link in re.findall(r'href="([^"]+)"', index) if 'torch-2.7.1%2Bcu128-cp312-cp312-win_amd64.whl' in link)
    url, expected = link.split('#sha256=')
    directory = ROOT / 'data' / 'wheels'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    if target.exists() and hashlib.file_digest(target.open('rb'), 'sha256').hexdigest() == expected:
        print(target)
        return
    request = urllib.request.Request(url, headers={'Range': 'bytes=0-0'})
    with urllib.request.urlopen(request, timeout=60) as response:
        size = int(response.headers['Content-Range'].split('/')[-1])
    chunk_size = 16 * 1024 * 1024
    segments = [(i, min(i + chunk_size, size)) for i in range(0, size, chunk_size)]

    def part(segment):
        start, end = segment
        file = directory / f'torch-part-{start}'
        if file.exists() and file.stat().st_size == end-start:
            return file
        for attempt in range(5):
            try:
                # Explicit ranges avoid long stalled full-file responses on some networks.
                req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end-1}'})
                with urllib.request.urlopen(req, timeout=90) as response:
                    if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {start}-{end-1}/'):
                        raise ValueError('Invalid range response')
                    content = response.read(end-start+1)
                if len(content) != end-start:
                    raise ValueError('Incomplete range')
                file.write_bytes(content)
                return file
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 + attempt)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for n, _ in enumerate(pool.map(part, segments), 1):
            if n % 8 == 0:
                print(f'{min(n*chunk_size,size)/1e9:.1f}/{size/1e9:.1f} GB', flush=True)
    with target.open('wb') as output:
        for start, end in segments:
            output.write((directory / f'torch-part-{start}').read_bytes())
    with target.open('rb') as source:
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError('Wheel SHA256 does not match the official index')
    print('SHA256 verified: ' + actual, flush=True)
    # Only delete precisely named verified download chunks inside this cache.
    for start, end in segments:
        (directory / f'torch-part-{start}').unlink()
    print(target, flush=True)


if __name__ == '__main__':
    main()
