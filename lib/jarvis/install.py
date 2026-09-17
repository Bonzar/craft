"""Раскладка установки: что установщик записал и где это лежит.

Установщик кладёт в корень установки один файл `install.json`. Библиотека
читает его, чтобы найти каталог состояния, оба конфига режима и `lib`-части
установленных модулей. Ничего из этого модуль не ищет сам.
"""

import json
from dataclasses import dataclass
from pathlib import Path

INSTALL_MANIFEST = 'install.json'

# Каталоги внутри корня установки. Имена закреплены: по ним и установщик
# раскладывает части, и библиотека их потом находит.
JARVIS_DIR = 'jarvis'
LIB_DIR = 'lib'
MODULES_DIR = 'modules'


def jarvis_root(settings_root: Path | str) -> Path:
    return Path(settings_root) / JARVIS_DIR


def manifest_path(settings_root: Path | str) -> Path:
    return jarvis_root(settings_root) / INSTALL_MANIFEST


@dataclass(frozen=True)
class Install:
    """Прочитанный `install.json`."""

    settings_root: Path
    state_dir: Path
    personal_config: Path | None
    source_config: Path | None
    lib_parts: dict[str, str]
    modules: dict[str, dict]

    @property
    def lib_dir(self) -> Path:
        return jarvis_root(self.settings_root) / LIB_DIR

    @property
    def modules_dir(self) -> Path:
        return jarvis_root(self.settings_root) / MODULES_DIR

    def module_dir(self, slug: str) -> Path:
        return self.modules_dir / slug


def load(settings_root: Path | str) -> Install:
    path = manifest_path(settings_root)
    raw = json.loads(path.read_text(encoding='utf-8'))
    personal = raw.get('personal_config')
    source = raw.get('source_config')
    return Install(
        settings_root=Path(settings_root),
        state_dir=Path(raw['state_dir']),
        personal_config=Path(personal) if personal else None,
        source_config=Path(source) if source else None,
        lib_parts=dict(raw.get('lib_parts', {})),
        modules=dict(raw.get('modules', {})),
    )
