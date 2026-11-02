"""Слова одной команды Craft: флаги со значениями и аргументы после глагола.

Флаг цели, позиции или состояния ищется по словам команды, а не по её
тексту: `--state done` внутри `--markdown` или `--json` — текст нагрузки, и
флагом он не становится. Слова режутся как в оболочке; незакрытая кавычка
оболочке не по силам, тогда слова режутся терпимо: кавычка тянется до конца
команды, обратный слеш экранирует следующий символ, как в пачке (batch.py).
"""

import shlex
from typing import NamedTuple

FLAG_PREFIX = '--'
FLAG_GLUE = '='
QUOTES = '\'"'
ESCAPE = '\\'
# Слов в глаголе: `blocks add`, `tasks update`.
VERB_WORDS = 2
# Флаги нагрузки: значение у них есть всегда, даже если оно начинается с «--».
PAYLOAD_FLAGS = ('json', 'markdown')


class Parsed(NamedTuple):
    """Флаги `(имя без «--», значение или None)` по порядку и аргументы после глагола."""
    flags: tuple[tuple[str, str | None], ...]
    arguments: tuple[str, ...]

    def values(self, name: str) -> list[str | None]:
        return [value for flag, value in self.flags if flag == name]

    def value(self, name: str) -> str | None:
        """Значение первого такого флага; нет флага или значения — None."""
        found = self.values(name)
        return found[0] if found else None

    def has(self, name: str) -> bool:
        return any(flag == name for flag, _ in self.flags)


def loose_words(command: str) -> list[str]:
    """Слова без строгости оболочки: кавычки сняты, экранированный символ остаётся с слешем."""
    words, current, quote, escaped, started = [], [], None, False, False
    for char in command:
        if escaped:
            current.append(char)
            escaped = False
        elif char == ESCAPE:
            current.append(char)
            escaped = started = True
        elif quote is None and char.isspace():
            if started:
                words.append(''.join(current))
            current, started = [], False
        elif char in QUOTES and quote in (None, char):
            quote = None if quote == char else char
            started = True
        else:
            current.append(char)
            started = True
    if started:
        words.append(''.join(current))
    return words


def words_of(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return loose_words(command)


def parse_command(command: str) -> Parsed:
    """Флаги и аргументы одной команды; значение флага нагрузки флагов не несёт."""
    rest = words_of(command)[VERB_WORDS:]
    flags, arguments, index = [], [], 0
    while index < len(rest):
        word = rest[index]
        index += 1
        if not word.startswith(FLAG_PREFIX) or word == FLAG_PREFIX:
            arguments.append(word)
            continue
        name, glue, value = word[len(FLAG_PREFIX):].partition(FLAG_GLUE)
        if glue:
            flags.append((name, value))
            continue
        following = rest[index] if index < len(rest) else None
        if following is not None and (name in PAYLOAD_FLAGS or not following.startswith(FLAG_PREFIX)):
            flags.append((name, following))
            index += 1
        else:
            flags.append((name, None))
    return Parsed(tuple(flags), tuple(arguments))
