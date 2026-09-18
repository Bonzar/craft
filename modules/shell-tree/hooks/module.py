#!/usr/bin/env python3
"""Сборка бинарника разбора: модуль собирает себя сам на старте сессии.

Модуль самодостаточен — ни сборки, ни копирования данных в setup-скрипте
окружения нет. Нужен только Go на PATH: остальное модуль делает своим хуком.

Работа хука вся в одном решении: отпечаток исходников из `go/` сошёлся с тем,
что записан рядом с бинарником, — делать нечего; не сошёлся или бинарника нет —
собрать. Поэтому холодный старт стоит секунды, а тёплый — доли секунды.

Пока сборка идёт, в зоне сессии лежит маркер с её pid: по нему библиотечная
часть отличает «собирается, подожди» от «не собран и не соберётся». Секунд в
ожидании нет — есть живой процесс, как у копий стартового контекста.

Наружу хук всегда молчит. Ни отсутствие Go, ни падение сборки не роняют сессию:
причина уходит в журнал модуля в зоне сессии, а модель про сборку узнавать не
должна — ей нужен инструмент, а не отчёт о нём. Там же лежит и замер времени.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis
from jarvis import registry
from jarvis.wrappers import claude

GO = 'go'
BUILD_FLAGS = ['build', '-ldflags=-s -w']


class Module(jarvis.Module):
    """Сборка на старте сессии. Ответ харнесу — всегда молчание."""

    def handle(self, event, runtime):
        library = load_library(runtime)
        if library is None:
            note(runtime, event, action='нет библиотечной части', reason='lib модуля не найдена')
            return jarvis.Silence()
        try:
            build(library, runtime, event)
        except Exception as failure:
            # Сессию сборка не роняет: причина с контекстом уходит в журнал, а
            # харнес получает молчание. Инструмента не будет — об этом скажет
            # библиотечная часть тому, кто её позовёт.
            note(runtime, event, action='сборка не вышла',
                 reason=f'{type(failure).__name__}: {failure}')
        return jarvis.Silence()


def load_library(runtime):
    """Своя lib-часть: в ней живут пути модуля и имена маркеров."""
    found = registry.library_of(runtime.manifest)
    return found.load() if found is not None else None


def build(library, runtime, event) -> None:
    """Собрать бинарник, если он отстал от исходников."""
    source = library.SOURCE_DIR
    binary = library.BINARY
    if not source.is_dir():
        note(runtime, event, action='нет исходников', reason=f'{source} не каталог')
        return

    want = library.fingerprint(source)
    if binary.is_file() and recorded(library) == want:
        note(runtime, event, action='сборка не нужна', reason='отпечаток исходников сошёлся')
        return

    tool = shutil.which(GO)
    if tool is None:
        note(runtime, event, action='нет go',
             reason='go на PATH не найден: бинарник не собран, разбор будет недоступен')
        return

    binary.parent.mkdir(parents=True, exist_ok=True)
    mark(library, runtime, library.BUILDING)
    started = time.monotonic()
    # Срока у сборки нет намеренно: пока она идёт, поток не умирает по таймеру,
    # а общий предел на ход хука ставит харнес строкой установщика.
    done = subprocess.run(
        [tool, *BUILD_FLAGS, '-o', str(binary), '.'],
        cwd=str(source), env={**os.environ, 'CGO_ENABLED': '0'},
        capture_output=True, text=True, check=False,
    )
    seconds = round(time.monotonic() - started, 3)
    if done.returncode != 0:
        mark(library, runtime, library.READY)
        note(runtime, event, action='сборка упала', seconds=seconds,
             reason=f'go build вернул {done.returncode}: {done.stderr.strip()[:300]}')
        return

    library.FINGERPRINT.write_text(
        json.dumps({'fingerprint': want, 'at': time.time()}, ensure_ascii=False),
        encoding='utf-8',
    )
    mark(library, runtime, library.READY)
    note(runtime, event, action='собран', seconds=seconds, reason=str(binary))


def recorded(library) -> str:
    """Отпечаток, записанный рядом с бинарником. Нет записи — пустая строка."""
    try:
        return json.loads(library.FINGERPRINT.read_text(encoding='utf-8')).get('fingerprint', '')
    except (OSError, ValueError):
        return ''


def mark(library, runtime, state: str) -> None:
    """Маркер сборки в зоне сессии: состояние и pid того, кто собирает."""
    runtime.storage.write_json(
        library.MARKER_FILE,
        {'state': state, 'pid': os.getpid(), 'at': time.time()},
    )


def note(runtime, event, action: str, reason: str = '', seconds=None) -> None:
    """Журнал модуля: по строке на ход, с замером времени сборки."""
    entry = {'at': time.time(), 'event': getattr(event, 'event', None),
             'action': action, 'reason': reason}
    if seconds is not None:
        entry['seconds'] = seconds
    library = load_library(runtime)
    name = library.JOURNAL_FILE if library is not None else 'shell-tree.jsonl'
    runtime.storage.append_line(name, json.dumps(entry, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(claude.run_hook(__file__, Module))
