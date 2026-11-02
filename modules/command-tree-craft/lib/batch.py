"""Пачка команд Craft: команды через «;», как их режет сам Craft.

Парсер пачки Craft учитывает кавычки: «;» внутри строки полезной нагрузки
(`--markdown`, `--json`) команду не режет. Обратный слеш экранирует
следующий символ: экранированная кавычка строку не закрывает.
"""

SEPARATOR = ';'
QUOTES = '\'"'
ESCAPE = '\\'


def split_commands(text: str) -> list[str]:
    """Команды пачки без пробелов по краям; пустые между «;» отброшены."""
    commands, current, quote, escaped = [], [], None, False
    for char in text:
        if escaped:
            escaped = False
        elif char == ESCAPE:
            escaped = True
        elif quote is None and char == SEPARATOR:
            commands.append(''.join(current))
            current = []
            continue
        elif char in QUOTES and quote in (None, char):
            quote = None if quote == char else char
        current.append(char)
    commands.append(''.join(current))
    return [command.strip() for command in commands if command.strip()]
