"""Runtime contract of the `craft-sync` module.

The binary is never discovered globally: consumers resolve this module through
the registry and ask this library for its executable.  While SessionStart is
building it, they wait for the live builder instead of racing it.
"""

import hashlib
import json
import os
import time
from pathlib import Path

BINARY_NAME = 'craft-sync'
MODULE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = MODULE_DIR / 'src'
BINARY = MODULE_DIR / 'bin' / BINARY_NAME
FINGERPRINT = MODULE_DIR / 'bin' / 'fingerprint.json'
SOURCE_GLOBS = ('*.go', 'go.mod', 'go.sum')
MARKER_FILE = 'craft-sync-build.json'
JOURNAL_FILE = 'craft-sync.jsonl'
BUILDING = 'сборка идёт'
READY = 'готов'
# Only a test seam. Production has exactly one location for the executable.
BINARY_ENV = 'CRAFT_SYNC_BIN'
WAIT_STEP = 0.05


class CraftSyncError(RuntimeError):
    """The module executable is not available."""


def fingerprint(source: Path | None = None) -> str:
    source = Path(source) if source is not None else SOURCE_DIR
    digest = hashlib.sha256()
    files = []
    for pattern in SOURCE_GLOBS:
        files += sorted(source.glob(pattern))
    for path in sorted(set(files)):
        digest.update(path.name.encode('utf-8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def still_running(pid) -> bool:
    if not isinstance(pid, int):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_build(storage) -> None:
    if storage is None:
        return
    while True:
        if BINARY.is_file():
            return
        mark = storage.read_json(MARKER_FILE, default=None) or {}
        if mark.get('state') != BUILDING or not still_running(mark.get('pid')):
            return
        time.sleep(WAIT_STEP)


def binary(storage=None) -> str:
    """Return this module's executable or explain why SessionStart did not make it."""
    override = os.environ.get(BINARY_ENV)
    if override and Path(override).expanduser().is_file():
        return str(Path(override).expanduser())
    if BINARY.is_file():
        return str(BINARY)
    wait_for_build(storage)
    if BINARY.is_file():
        return str(BINARY)
    raise CraftSyncError(
        f'{BINARY_NAME} не собран: {BINARY} нет, и сборка не идёт. '
        'Бинарник собирает хук модуля на старте сессии; нужен Go на PATH'
    )
