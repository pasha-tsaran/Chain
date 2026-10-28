"""Create a source archive without models, personal articles or credentials."""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    target = output / 'Redakciya-source.zip'
    files = [p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.py', '.md', '.cmd', '.txt')]
    for folder in ('static', 'tests', 'docs', 'examples'):
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(files):
            archive.write(file, 'Redakciya/' + file.relative_to(ROOT).as_posix())
        archive.write(ROOT / '.gitignore', 'Redakciya/.gitignore')
    print(target)


if __name__ == '__main__':
    main()
