"""Теги в команде записи Craft — из набора, который передал тот, кто зовёт.

Каких типов сущностей бывает, модуль не знает: набор тегов приносит
вызывающий, например замок типов по data/entity.json модулей сущностей.
"""

import re

TAG_PREFIX = '#'
# Агент может прислать JSON-полезную нагрузку с кириллицей в виде \uXXXX:
# тег там тот же, и пропускать его нельзя.
UNICODE_ESCAPE = re.compile(r'\\u([0-9a-fA-F]{4})')
# Текст ссылки — копия имени цели вместе с её тегами. Ссылка на этап из
# задачи этап не пишет, поэтому тег в тексте ссылки не считается.
BLOCK_LINK = re.compile(r'\[[^\]]*\]\((?:block|craftdocs)://[^)]*\)')


def tag_pattern(tags) -> re.Pattern:
    """Тег целым словом; хвост через слеш (#задача/готово) тоже тег.

    Теги приходят с решёткой. Регистр не важен: проверено ли, что Craft
    считает #Задача тем же тегом, мы не знаем, а лишняя находка дешевле
    пропуска.
    """
    names = '|'.join(re.escape(tag[1:]) for tag in sorted(tags, key=len, reverse=True))
    return re.compile(rf'(?<![\w#])#({names})(?![\w-])', re.IGNORECASE)


def decode_escapes(text: str) -> str:
    """`\\uXXXX` в символ: так тег в JSON-полезной нагрузке виден как есть."""
    return UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)


def strip_links(text: str) -> str:
    """Текст без ссылок на блок `[…](block://…)` и `[…](craftdocs://…)`."""
    return BLOCK_LINK.sub('', text)


def find_tags(command: str, tags) -> list[str]:
    """Теги набора в команде вне текста ссылок, в нижнем регистре, по алфавиту.

    Хвост тега (`#задача/готово`) в ответ не идёт: находка — тег набора.
    Пустой набор — пустой ответ.
    """
    tags = list(tags)
    if not tags:
        return []
    text = strip_links(decode_escapes(command))
    found = {TAG_PREFIX + match.group(1).lower() for match in tag_pattern(tags).finditer(text)}
    return sorted(found)
