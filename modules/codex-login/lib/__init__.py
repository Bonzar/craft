"""Где лежит codex, которого ставит модуль codex-login.

Программу модуль ставит себе сам в свою папку bin: так решено в разделе «Части
модуля», а глобальный PATH контрактом не является — там мог остаться codex от
другого снимка окружения. Кому ещё нужен codex, называет codex-login в requires
и берёт путь отсюда.
"""

from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
# npm ставит пакет с --global --prefix в <prefix>/bin и <prefix>/lib. Свой
# prefix внутри bin, а не сама папка модуля: lib в корне модуля — его часть.
PREFIX = MODULE_DIR / 'bin' / 'npm'
BINARY = PREFIX / 'bin' / 'codex'
PACKAGE = '@openai/codex'
JOURNAL_FILE = 'codex-login.jsonl'


class CodexError(RuntimeError):
    """codex в папке модуля нет."""


def binary() -> Path:
    if not BINARY.is_file():
        raise CodexError(f'codex не поставлен: нет {BINARY}')
    return BINARY
