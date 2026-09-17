"""Поиск библиотечного модуля по slug из requires.

Модуль зовёт библиотечный модуль по slug, путь к нему находит библиотека.
Собранная библиотека одна, копий нет: `lib`-часть каждого модуля лежит в ней
подкаталогом со своим slug, поэтому поиск по slug — это поиск подкаталога.

Модуля нет — здесь возвращается None, а зависимый говорит об этом в чате и
молчит. Тихой подмены соседним модулем не делается.
"""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .install import Install


@dataclass(frozen=True)
class LibraryModule:
    """Найденная `lib`-часть: её slug и каталог в собранной библиотеке."""

    slug: str
    path: Path

    def load(self) -> ModuleType:
        """Импортировать часть как пакет `jarvis_lib.<slug>`."""
        name = f'jarvis_lib.{self.slug.replace("-", "_")}'
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, self.path / '__init__.py')
        if spec is None or spec.loader is None:
            raise ImportError(f'lib-часть {self.slug} не импортируется из {self.path}')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            del sys.modules[name]  # полумодуль в кэше хуже, чем его отсутствие
            raise
        return module


def find(slug: str, install: Install) -> LibraryModule | None:
    """Библиотечный модуль по slug, либо None, если его не поставили."""
    relative = install.lib_parts.get(slug)
    if relative is None:
        return None
    path = install.lib_dir / relative
    if not path.is_dir():
        return None
    return LibraryModule(slug=slug, path=path)
