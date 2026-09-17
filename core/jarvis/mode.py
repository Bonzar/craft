"""Режим модуля: включён или выключен.

Модуль читает его через библиотеку на каждом событии из трёх источников по
старшинству: сессия в хранилище, личный конфиг, конфиг источника. Решает
первый, кто говорит про этот модуль; молчат все — модуль включён.

Правка действует со следующего события: режим читается на входе события и
внутри хода не перечитывается.

Файлы всех трёх источников — плоский JSON «slug → включён»:
`{"lock-irreversible-shell": false}`. Значение — настоящий булев JSON, и только
он: строка «false» и ноль отклоняются с ошибкой, а не приводятся к логическому.
Иначе написанное рукой «false» молча оставило бы замок включённым.

Конфиг источника лежит в каталоге модулей, рядом с папками самих модулей, и
переезжает вместе с ними: у коллеги без нашего установщика это тот же файл.
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
SOURCE_CONFIG_NAME = 'modules.json'


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


def _decide(slug: str, table: dict, source: str, where) -> ModeDecision | None:
    """Решение источника про этот slug. Источник молчит — None."""
    if slug not in table:
        return None
    value = table[slug]
    if not isinstance(value, bool):
        raise ValueError(
            f'{where}: режим модуля «{slug}» задан значением {value!r}; '
            'здесь должен быть настоящий true или false'
        )
    return ModeDecision(enabled=value, source=source)


def read(
    slug: str,
    storage: Storage,
    personal_config: Path | None = None,
    source_config: Path | None = None,
) -> ModeDecision:
    """Первый источник, который говорит про этот slug, и решает."""
    session_modes = storage.read_json(SESSION_MODES_FILE, zone=SESSION, default={}) or {}
    for source, table, where in (
        (SOURCE_SESSION, session_modes, storage.path(SESSION_MODES_FILE, zone=SESSION)),
        (SOURCE_PERSONAL, _read_flat(personal_config), personal_config),
        (SOURCE_CONFIG, _read_flat(source_config), source_config),
    ):
        decision = _decide(slug, table, source, where)
        if decision is not None:
            return decision
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
