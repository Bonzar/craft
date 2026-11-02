"""Отладочный вывод ревью (временный): включён ли он и что не так с его файлом.

При data/debug.json с {"debug": true} под показанным ответом дописывается
итог ревью. Точка входа хука — hooks/module.py, здесь только чтение флага.
"""

import json
from pathlib import Path

DEBUG_FILE = Path(__file__).resolve().parents[1] / 'data' / 'debug.json'
DEBUG_FIELD = 'debug'


def read_debug() -> tuple[bool, str | None]:
    """Включён ли отладочный вывод и что не так с его файлом.

    Читается на каждом событии: переключать можно посреди сессии.
    """
    try:
        data = json.loads(DEBUG_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return False, None
    except ValueError as error:
        return False, f'файл {DEBUG_FILE} не JSON ({error}): вывод выключен'
    return isinstance(data, dict) and data.get(DEBUG_FIELD) is True, None


def debug_on() -> bool:
    return read_debug()[0]
