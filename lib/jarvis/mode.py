"""Режим модуля: включён или выключен.

Модуль читает его через библиотеку на каждом событии из трёх источников по
старшинству: сессия в хранилище, личный конфиг, конфиг источника. Решает
первый, кто говорит про этот модуль; молчат все — модуль включён.

Правка действует со следующего события: режим читается на входе события и
внутри хода не перечитывается.

Файлы всех трёх источников — плоский JSON «slug → включён»:
`{"lock-irreversible-shell": false}`.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .storage import SESSION, Storage

SOURCE_SESSION = 'session'
SOURCE_PERSONAL = 'personal'
SOURCE_CONFIG = 'source'
SOURCE_DEFAULT = 'default'

SESSION_MODES_FILE = 'modes.json'


@dataclass(frozen=True)
class ModeDecision:
    """Решение о режиме и то, кто его вынес: обоими полями модуль пишет след."""

    enabled: bool
    source: str


def _read_flat(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (FileNotFoundError, NotADirectoryError):
        return {}
    if not text.strip():
        return {}
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f'конфиг режимов {path} должен быть объектом «slug → включён»')
    return value


def read(
    slug: str,
    storage: Storage,
    personal_config: Path | None = None,
    source_config: Path | None = None,
) -> ModeDecision:
    """Первый источник, который говорит про этот slug, и решает."""
    session_modes = storage.read_json(SESSION_MODES_FILE, zone=SESSION, default={}) or {}
    for source, table in (
        (SOURCE_SESSION, session_modes),
        (SOURCE_PERSONAL, _read_flat(personal_config)),
        (SOURCE_CONFIG, _read_flat(source_config)),
    ):
        if slug in table:
            return ModeDecision(enabled=bool(table[slug]), source=source)
    return ModeDecision(enabled=True, source=SOURCE_DEFAULT)


def set_session(slug: str, enabled: bool, storage: Storage) -> None:
    """Выключатель на сессию: им пользуются и реплика Влада, и автономный прогон.

    Автономный прогон задаёт выключенные модули до первого события — этой же
    записью, пока хранилище сессии ещё пустое.
    """
    modes = storage.read_json(SESSION_MODES_FILE, zone=SESSION, default={}) or {}
    updated = dict(modes)
    updated[slug] = bool(enabled)
    storage.write_json(SESSION_MODES_FILE, updated, zone=SESSION)
