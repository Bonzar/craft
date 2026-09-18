"""Поиск соседей по каталогу модулей.

Модуль находит соседей от собственной папки, а не от аргумента установщика:
харнес всегда запускает файл модуля, и путь к себе у этого файла есть. Поэтому
раскладка одна и там, где ставил наш установщик, и там, куда модули приехали
набором — у коллеги в aisuite установщика нет.

Набор для шеринга состоит только из модулей, все одного вида. Ядро едет внутри
каждого модуля папкой `_core`: с подчёркивания, а значит slug'ом она быть не
может, и запрещать имена не приходится. Модуль самодостаточен, поэтому разные
модули одного набора могут нести разные версии ядра.

Каталог модулей — родитель папки модуля; по нему и ищутся соседи.
Библиотечная часть остаётся внутри папки своего модуля — `‹slug›/lib`.
"""

import importlib.machinery
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .manifest import MANIFEST_NAME, Manifest, adapters_of, load, matches

# Папка ядра внутри папки модуля.
CORE_DIR = '_core'
LIB_PART = 'lib'
DATA_PART = 'data'

# Родительский пакет для библиотечных частей. Регистрируется до загрузки части,
# иначе обычная форма пакета `from . import helper` внутри неё не разрешается.
PARENT_PACKAGE = 'jarvis_lib'


def modules_dir(module_dir: Path | str) -> Path:
    """Каталог модулей — тот, в котором лежит папка модуля."""
    return Path(module_dir).resolve().parent


def neighbours(module_dir: Path | str) -> list[Manifest]:
    """Шапки всех модулей каталога, включая сам вызвавший.

    Папки без шапки (в том числе `_core`) модулями не считаются.
    """
    found = []
    for child in sorted(modules_dir(module_dir).iterdir()):
        if child.is_dir() and (child / MANIFEST_NAME).is_file():
            found.append(load(child))
    return found


@dataclass(frozen=True)
class LibraryModule:
    """Найденная `lib`-часть: slug её модуля и каталог части."""

    slug: str
    path: Path

    def load(self) -> ModuleType:
        name = f'{PARENT_PACKAGE}.{self.slug.replace("-", "_")}'
        if name in sys.modules:
            return sys.modules[name]
        _ensure_parent_package()
        spec = importlib.util.spec_from_file_location(
            name,
            self.path / '__init__.py',
            submodule_search_locations=[str(self.path)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f'lib-часть {self.slug} не импортируется из {self.path}')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            del sys.modules[name]  # полумодуль в кэше хуже, чем его отсутствие
            raise
        setattr(sys.modules[PARENT_PACKAGE], name.rsplit('.', 1)[1], module)
        return module


def _ensure_parent_package() -> ModuleType:
    """Завести пустой пакет-родитель, если его ещё нет."""
    parent = sys.modules.get(PARENT_PACKAGE)
    if parent is None:
        spec = importlib.machinery.ModuleSpec(PARENT_PACKAGE, None, is_package=True)
        parent = importlib.util.module_from_spec(spec)
        parent.__path__ = []
        sys.modules[PARENT_PACKAGE] = parent
    return parent


def find(requirement: str, module_dir: Path | str) -> LibraryModule | None:
    """Библиотечный модуль по требованию из requires — рядом, по каталогу модулей.

    Требование ищется ровно так, как записано: голый slug — по slug, маска
    семейства — по slug и по полю `for` адаптера. Ничего не нашлось — None, и
    зависимый говорит об этом в чате и молчит. Тихой подмены соседом нет.
    """
    for manifest in neighbours(module_dir):
        if not matches(requirement, manifest) or manifest.path is None:
            continue
        part = manifest.path / LIB_PART
        if part.is_dir():
            return LibraryModule(slug=manifest.slug, path=part)
    return None


def library_of(manifest: Manifest) -> LibraryModule | None:
    """Библиотечная часть этого модуля. Части нет — None."""
    if manifest.path is None:
        return None
    part = manifest.path / LIB_PART
    return LibraryModule(slug=manifest.slug, path=part) if part.is_dir() else None


def data_of(manifest: Manifest) -> Path | None:
    """Часть данных этого модуля. Части нет — None."""
    if manifest.path is None:
        return None
    part = manifest.path / DATA_PART
    return part if part.is_dir() else None


def adapters(module_dir: Path | str, slug: str) -> list[Manifest]:
    """Адаптеры базы: соседи, чей `for` накрывает её slug.

    Это единственное, что у баз общее, — и потому лежит в ядре, а не в модуле:
    как искать своих адаптеров, знают все базы одинаково, а что делать с
    найденным — каждая своё.
    """
    return adapters_of(slug, neighbours(module_dir))
