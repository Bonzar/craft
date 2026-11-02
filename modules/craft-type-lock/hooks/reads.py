"""Что агент прочитал: ответ `craft_read` разобран в страницы и их детей.

Часть замка craft-type-lock. Ответ инструмента Craft MCP в событии
`post-tool` — список кусков `{"type": "text", "text": …}` (замер 02.11.2026 по
записям ревью хода у `craft_write` того же сервера); строка и словарь с
`content` тоже разбираются, на случай другой обёртки харнеса.

Два вида чтения (замер 02.11.2026 живыми `craft_read`):

- JSON (`--format json`): `{"data": [корень], "nextCursor": …}`. У каждого
  блока `id`, `type`, `markdown`, `indentationLevel` (поля нет — уровень 0);
  у страницы в пределах глубины — `content` с детьми по порядку, за
  пределами — только `contentPreviewMd`. Прямых детей корня — до 50 за раз,
  дальше курсор `--cursor 50:…`; ответ по курсору снова отдаёт корень, а в
  `content` — следующие дети. Где лежит `nextCursor` в JSON, сказано справкой
  (`blocks get --help`), а не замерено: поэтому полный список детей ещё и
  короче 50.
- markdown (по умолчанию): страницы тегами `<page id="…">` с `<pageTitle>`
  внутри `<content>` своей страницы, id есть только у страниц, а отступ
  страниц не виден вовсе: этапы на +1 и задачи на +2 стоят в тексте
  одинаково. Поэтому из markdown берётся только вложенность страниц.
"""

import json
import re
from typing import NamedTuple

ID_KEY = 'id'
CONTENT_KEY = 'content'
DATA_KEY = 'data'
CURSOR_KEY = 'nextCursor'
TEXT_KEY = 'text'
# Поля блока, которые нужны замку: тип, текст с тегами и чекбоксом, отступ.
KEPT_FIELDS = ('id', 'type', 'markdown', 'indentationLevel')
JSON_START = re.compile(r'\{\s*"data"\s*:')
PAGE_OPEN = re.compile(r'<page id="([^"]+)"[^>]*>')
PAGE_CLOSE = '</page>'
# Курсор продолжения: `--cursor 50:‹ключ›` — дети с пятидесятого.
CURSOR_FLAG = 'cursor'
CURSOR_OFFSET = re.compile(r'^(\d+):')


class JsonRead(NamedTuple):
    """Корень JSON-чтения и курсор продолжения его детей (None — детей больше нет)."""
    root: dict
    next_cursor: str | None


def response_text(result) -> str:
    """Текст ответа MCP: строка, список кусков `{type, text}` или словарь с `content`."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return response_text(result.get(CONTENT_KEY, result.get(TEXT_KEY, '')))
    if isinstance(result, list):
        return '\n'.join(response_text(item) if not isinstance(item, dict)
                         else str(item.get(TEXT_KEY, '')) for item in result)
    return ''


def slim(block: dict) -> dict:
    """Блок без детей и превью: только поля, по которым судится место."""
    return {key: block[key] for key in KEPT_FIELDS if key in block}


def is_tree(node) -> bool:
    """Узел с id; его дети, если есть, — список узлов с id."""
    if not isinstance(node, dict) or not isinstance(node.get(ID_KEY), str):
        return False
    content = node.get(CONTENT_KEY)
    return content is None or (isinstance(content, list) and all(is_tree(child) for child in content))


def json_reads(text: str) -> list[JsonRead]:
    """Все JSON-ответы в тексте: пачка чтений даёт по ответу на команду.

    Кусок, который не разбирается как JSON или не дерево блоков, пропущен:
    это чтение не JSON, и о нём скажет разбор markdown.
    """
    decoder, found = json.JSONDecoder(), []
    for match in JSON_START.finditer(text):
        try:
            answer, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        cursor = answer.get(CURSOR_KEY)
        for root in answer.get(DATA_KEY) or []:
            if is_tree(root):
                found.append(JsonRead(root, cursor if isinstance(cursor, str) and cursor else None))
    return found


def markdown_pages(text: str) -> list[tuple[str, str | None]]:
    """Страницы markdown-ответа парами (страница, страница над ней или None)."""
    stack, found = [], []
    for line in text.splitlines():
        for match in PAGE_OPEN.finditer(line):
            found.append((match.group(1), stack[-1] if stack else None))
            stack.append(match.group(1))
        for _ in range(line.count(PAGE_CLOSE)):
            if stack:
                stack.pop()
    return found


def cursor_offsets(command: str, craft) -> dict[str, int]:
    """Чтения по курсору: id страницы → номер первого ребёнка в ответе."""
    offsets = {}
    for part in craft.split_commands(command) or [command]:
        cursor = craft.parse_command(part).value(CURSOR_FLAG)
        match = CURSOR_OFFSET.match((cursor or '').strip('\'"'))
        ids = craft.block_ids(part)
        if match and ids:
            offsets[ids[0].lower()] = int(match.group(1))
    return offsets
