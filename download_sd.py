"""Verified resumable SD-Turbo download when full-file transfers stall."""
import concurrent.futures
import hashlib
import json
import shutil
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = 'stabilityai/sd-turbo'


def main():
    tree = json.load(urllib.request.urlopen('https://huggingface.co/api/models/' + REPO + '/tree/main?recursive=true', timeout=60))
    folder = ROOT / 'models' / 'sd-turbo'
    cache = ROOT / 'models' / 'huggingface' / 'hub' / 'models--stabilityai--sd-turbo' / 'blobs'
    for entry in tree:
        path = entry['path']
        if entry['type'] != 'file' or not (path.endswith('.json') or path.startswith('tokenizer/') or path.endswith('.fp16.safetensors')):
            continue
        relative = Path(path)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid model repository path')
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = entry.get('lfs', {}).get('oid')
        size = entry['size']
        def valid(file):
            if not file.is_file() or file.stat().st_size != size:
                return False
            if expected:
                with file.open('rb') as source:
                    return hashlib.file_digest(source, 'sha256').hexdigest() == expected
            with file.open('rb') as source:
                data = source.read()
            return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == entry['oid']
        if valid(target):
            continue
        blob = cache / (expected or entry['oid'])
        if valid(blob):
            shutil.copyfile(blob, target)
            continue
        url = 'https://huggingface.co/' + REPO + '/resolve/main/' + path
        if size < 4 * 1024 * 1024:
            with urllib.request.urlopen(url, timeout=90) as response:
                target.write_bytes(response.read())
        else:
            chunk_size = 8 * 1024 * 1024
            segments = [(start, min(start + chunk_size, size)) for start in range(0, size, chunk_size)]
            def download(segment):
                start, end = segment
                part = target.with_name(target.name + f'.part-{start}')
                if part.exists() and part.stat().st_size == end-start:
                    return part
                for attempt in range(6):
                    try:
                        req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end-1}'})
                        with urllib.request.urlopen(req, timeout=90) as response:
                            if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {start}-{end-1}/'):
                                raise ValueError('Invalid range response')
                            content = response.read(end-start+1)
                        if len(content) != end-start:
                            raise ValueError('Incomplete range')
                        part.write_bytes(content)
                        return part
                    except Exception:
                        if attempt == 5:
                            raise
                        time.sleep(2+attempt)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                for number, _ in enumerate(pool.map(download, segments), 1):
                    if number % 16 == 0:
                        print(f'{path}: {min(number*chunk_size,size)/1e9:.1f}/{size/1e9:.1f} GB', flush=True)
            with target.open('wb') as output:
                for start, end in segments:
                    output.write(target.with_name(target.name+f'.part-{start}').read_bytes())
        if not valid(target):
            raise ValueError('Checksum mismatch: ' + path)
        for part in target.parent.glob(target.name + '.part-*'):
            part.unlink()
        print('Verified ' + path, flush=True)
    print('SD-Turbo downloaded and verified', flush=True)


if __name__ == '__main__':
    main()
