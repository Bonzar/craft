#!/usr/bin/env python3
"""Модуль-проба: проходит путь целиком и ничего не решает.

Отвечает молчанием на своём событии. Смысл пробы — в следе: по нему видно, что
модуль установлен, обёртка довела до него событие, библиотека прочла режим и
признак автономии и записала ответ.

Файл запускает сам харнес — он и есть точка входа модуля. Ядро ищется от пути
к этому файлу: `hooks/` лежит в папке модуля, и там же рядом папка ядра
`_core`. Модуль самодостаточен: установщик для этого не нужен, поэтому он
работает и там, куда его просто скопировали папкой.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis.wrappers import claude  # noqa: E402


class Module(jarvis.Module):
    """Молчание — законный ответ, и он тоже пишется в след."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        return jarvis.Silence()


if __name__ == '__main__':
    sys.exit(claude.run_hook(__file__, Module))
