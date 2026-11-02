"""Id блоков Craft в команде и в ответе чтения.

Регистр id API различает (замер 01.11.2026: у документа с id в верхнем
регистре нижний отвечает 404, и наоборот). Поэтому id отдаются как есть, а
повторы снимаются без учёта регистра.
"""

import re

BLOCK_ID = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')
# Страница из ответа чтения: в markdown-ответе это теги страниц, в
# JSON-ответе первое поле id — корень прочитанного.
PAGE_TAG = re.compile(r'<page id="([^"]+)"')
JSON_ROOT_ID = re.compile(r'"id":\s*"([^"]+)"')


def is_block_id(text) -> bool:
    """Строка целиком — id блока."""
    return isinstance(text, str) and BLOCK_ID.fullmatch(text) is not None


def unique_ids(ids) -> list[str]:
    """Без повторов в любом регистре, порядок и регистр первого вхождения."""
    seen, result = set(), []
    for block_id in ids:
        if block_id.lower() not in seen:
            seen.add(block_id.lower())
            result.append(block_id)
    return result


def block_ids(text: str) -> list[str]:
    """Id блоков из текста, без повторов, в порядке появления."""
    return unique_ids(match.group(0) for match in BLOCK_ID.finditer(text))


def answer_page_ids(answer: str) -> list[str]:
    """Страницы из ответа craft_read: теги `<page id>`, за ними корень JSON.

    `blocks get --date today` id в команде не несёт, и страницу прочитанного
    знает только ответ. Значения, на id блока не похожие, отброшены; повторы
    остаются — их снимает unique_ids у того, кто зовёт.
    """
    root = JSON_ROOT_ID.search(answer)
    pages = [*PAGE_TAG.findall(answer), *([root.group(1)] if root else [])]
    return [page for page in pages if is_block_id(page)]
