#!/usr/bin/env python3
"""Build `craft-sync` inside its module on SessionStart.

The installer only copies and registers modules.  This hook owns rebuilding,
the source fingerprint, and the session marker which lets dependants wait for
an in-progress build.
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
from jarvis import registry, wrappers

GO = 'go'
BUILD_FLAGS = ['build', '-ldflags=-s -w']


class Module(jarvis.Module):
    def handle(self, event, runtime):
        library = load_library(runtime)
        if library is None:
            note(runtime, event, 'нет библиотечной части', 'lib модуля не найдена')
            return jarvis.Silence()
        try:
            build(library, runtime, event)
        except Exception as failure:
            note(runtime, event, 'сборка не вышла', f'{type(failure).__name__}: {failure}')
        return jarvis.Silence()


def load_library(runtime):
    found = registry.library_of(runtime.manifest)
    return found.load() if found is not None else None


def build(library, runtime, event) -> None:
    if not library.SOURCE_DIR.is_dir():
        note(runtime, event, 'нет исходников', f'{library.SOURCE_DIR} не каталог')
        return
    want = library.fingerprint()
    if library.BINARY.is_file() and recorded(library) == want:
        note(runtime, event, 'сборка не нужна', 'отпечаток исходников сошёлся')
        return
    go = shutil.which(GO)
    if go is None:
        note(runtime, event, 'нет go', 'go на PATH не найден: craft-sync не собран')
        return
    library.BINARY.parent.mkdir(parents=True, exist_ok=True)
    temporary = library.BINARY.with_name(f'.{library.BINARY.name}.{os.getpid()}.tmp')
    mark(library, runtime, library.BUILDING)
    started = time.monotonic()
    done = subprocess.run(
        [go, *BUILD_FLAGS, '-o', str(temporary), '.'], cwd=str(library.SOURCE_DIR),
        env={**os.environ, 'CGO_ENABLED': '0'}, capture_output=True, text=True, check=False,
    )
    seconds = round(time.monotonic() - started, 3)
    if done.returncode:
        temporary.unlink(missing_ok=True)
        mark(library, runtime, library.READY)
        note(runtime, event, 'сборка упала',
             f'go build вернул {done.returncode}: {done.stderr.strip()[:300]}', seconds)
        return
    os.replace(temporary, library.BINARY)
    library.FINGERPRINT.write_text(json.dumps({'fingerprint': want, 'at': time.time()}), encoding='utf-8')
    mark(library, runtime, library.READY)
    note(runtime, event, 'собран', str(library.BINARY), seconds)


def recorded(library) -> str:
    try:
        return json.loads(library.FINGERPRINT.read_text(encoding='utf-8')).get('fingerprint', '')
    except (OSError, ValueError):
        return ''


def mark(library, runtime, state: str) -> None:
    runtime.storage.write_json(library.MARKER_FILE, {'state': state, 'pid': os.getpid(), 'at': time.time()})


def note(runtime, event, action: str, reason: str = '', seconds=None) -> None:
    entry = {'at': time.time(), 'event': getattr(event, 'event', None), 'action': action, 'reason': reason}
    if seconds is not None:
        entry['seconds'] = seconds
    library = load_library(runtime)
    runtime.storage.append_line(library.JOURNAL_FILE if library else 'craft-sync.jsonl',
                                json.dumps(entry, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
