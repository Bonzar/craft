"""Шапка модуля и разбор requires.

Шапка из четырёх полей, файл `module.toml` в корне папки модуля:

    slug = "lock-irreversible-shell"
    events = ["pre-tool", "prompt"]
    requires = ["shell-tree"]

`for` есть только у адаптеров, `events` — только у хуков. TOML взят потому, что
его читает стандартная библиотека Python 3.11 (`tomllib`), а зависимостей у нас
нет. Данных, режима, вида и привязки в шапке нет: вид модуля — это состав его
папки.

Требование ищется ровно так, как записано: голый slug — по slug, маска
`семейство-*` — по slug любого модуля семейства и по полю `for` адаптера,
который этому семейству служит.
"""

import fnmatch
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .events import ALL as ALL_EVENTS

MANIFEST_NAME = 'module.toml'
# Slug становится именем папки, поэтому он ограничен строчными латинскими
# буквами, цифрами и дефисом. Строчными — потому что на macOS файловая
# система регистронезависима и «Foo» столкнулось бы с «foo». Списка
# запрещённых имён нет и не нужно: папка ядра внутри модуля называется с
# подчёркивания, а его в slug быть не может.
SLUG_PATTERN = re.compile(r'^[a-z0-9-]+$')
FIELDS = ('slug', 'for', 'events', 'requires')
FAMILY_SUFFIX = '-*'


@dataclass(frozen=True)
class Manifest:
    """Прочитанная шапка. `path` — папка модуля, а не файл шапки."""

    slug: str
    events: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    serves: str | None = None  # поле `for`: `for` — ключевое слово Python
    path: Path | None = None


def _string_list(raw: Mapping, key: str, where: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f'{where}: поле {key} должно быть списком строк')
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f'{where}: в {key} должна быть непустая строка, а не {item!r}')
    return tuple(value)


def parse(text: str, where: str = MANIFEST_NAME, path: Path | None = None) -> Manifest:
    """Разбор шапки с проверкой на границе: невалидное отклоняется сразу."""
    raw = tomllib.loads(text)

    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise ValueError(f'{where}: в шапке нет полей {unknown}; есть только {list(FIELDS)}')

    slug = raw.get('slug')
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError(f'{where}: обязательное поле slug пусто или не строка')
    slug = slug.strip()
    if not SLUG_PATTERN.match(slug):
        raise ValueError(
            f'{where}: slug {slug!r} — только строчные латинские буквы, цифры и дефис: '
            'он становится именем папки'
        )

    events = _string_list(raw, 'events', where)
    unknown_events = [event for event in events if event not in ALL_EVENTS]
    if unknown_events:
        raise ValueError(
            f'{where}: события {unknown_events} нет в едином каталоге; есть {list(ALL_EVENTS)}'
        )

    serves = raw.get('for')
    if serves is not None and (not isinstance(serves, str) or not serves.strip()):
        raise ValueError(f'{where}: поле for либо непустая строка, либо его нет')

    return Manifest(
        slug=slug,
        events=events,
        requires=_string_list(raw, 'requires', where),
        serves=serves.strip() if isinstance(serves, str) else None,
        path=path,
    )


def load(module_dir: Path | str) -> Manifest:
    module_dir = Path(module_dir)
    manifest_path = module_dir / MANIFEST_NAME
    return parse(
        manifest_path.read_text(encoding='utf-8'),
        where=str(manifest_path),
        path=module_dir,
    )


def matches(requirement: str, manifest: Manifest) -> bool:
    """Отвечает ли модуль на это требование."""
    if requirement == manifest.slug:
        return True
    if not requirement.endswith(FAMILY_SUFFIX):
        return False
    if fnmatch.fnmatchcase(manifest.slug, requirement):
        return True
    if manifest.serves is None:
        return False
    family = requirement[: -len(FAMILY_SUFFIX)]
    return manifest.serves == family or fnmatch.fnmatchcase(manifest.serves, requirement)


def resolve(
    requirements: Iterable[str],
    installed: Iterable[Manifest],
) -> tuple[dict[str, list[Manifest]], tuple[str, ...]]:
    """Что нашлось по каждому требованию и чего не нашлось вовсе.

    Найденное показывается всегда: несопоставленные требования возвращаются
    отдельным списком, а не молча выпадают из результата.
    """
    installed = list(installed)
    found: dict[str, list[Manifest]] = {}
    missing: list[str] = []
    for requirement in requirements:
        hits = [manifest for manifest in installed if matches(requirement, manifest)]
        if hits:
            found[requirement] = hits
        else:
            missing.append(requirement)
    return found, tuple(missing)
