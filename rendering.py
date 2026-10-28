"""Small, escaped Markdown renderer for the supported article formatting."""
import html
import re


def inline(text):
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def render(markdown):
    output = []
    for block in re.split(r"\n\s*\n", markdown.strip()):
        lines = block.splitlines()
        if not lines:
            continue
        if len(lines) >= 2 and lines[0].strip().startswith('|') and re.match(r"^[\s|:\-]+$", lines[1]):
            rows = [[inline(c.strip()) for c in line.strip().strip('|').split('|')] for line in [lines[0], *lines[2:]]]
            output.append('<div class="table-scroll"><table><thead><tr>' + ''.join(f'<th>{c}</th>' for c in rows[0]) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows[1:]) + '</tbody></table></div>')
        elif all(re.match(r"^\s*[-*] ", line) for line in lines):
            output.append('<ul>' + ''.join('<li>' + inline(re.sub(r"^\s*[-*] ", '', line)) + '</li>' for line in lines) + '</ul>')
        elif all(re.match(r"^\s*\d+[.)] ", line) for line in lines):
            output.append('<ol>' + ''.join('<li>' + inline(re.sub(r"^\s*\d+[.)] ", '', line)) + '</li>' for line in lines) + '</ol>')
        elif block.startswith('> '):
            output.append('<blockquote>' + inline(block[2:]) + '</blockquote>')
        elif re.match(r'^#{1,3} ', block):
            level = min(3, len(block.split(' ')[0]))
            output.append(f'<h{level}>' + inline(block[level + 1:]) + f'</h{level}>')
        else:
            output.append('<p>' + inline(block).replace('\n', '<br>') + '</p>')
    return '\n'.join(output)


def plain_with_map(markdown):
    """Retain a source-character map so detection can patch exact raw spans."""
    skip = set()
    for pattern in (r'(?m)^#{1,6} ', r'(?m)^> ', r'(?m)^\s*[-*] ', r'(?m)^\s*\d+[.)] ', r'\*\*|\*|`', r'(?m)^\s*\|[ :\-|]+\|\s*$'):
        for match in re.finditer(pattern, markdown):
            skip.update(range(match.start(), match.end()))
    mapping = [i for i in range(len(markdown)) if i not in skip]
    return ''.join(markdown[i] for i in mapping), mapping


def article_markdown(article):
    return '# ' + article['title'] + '\n\n' + '\n\n'.join(b['markdown'] for b in article['blocks'])


def detector_text(article):
    return plain_with_map(article_markdown(article))[0]
