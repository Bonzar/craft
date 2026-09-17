"""Шапка модуля и разбор requires.

Шапка из шести полей, файл `module.toml` в корне папки модуля:

    slug = "lock-irreversible-shell"
    events = ["pre-tool", "prompt"]
    requires = ["shell-tree-*"]
    harness = "claude"
    copies = 1

- `slug` — имя модуля. От имени папки не зависит, по набору уникален.
- `for` — чему модуль служит: slug модулей и маски семейств. Только у адаптеров.
- `events` — на каких событиях модуль стоит. Только у хуков.
- `requires` — что модулю нужно: slug или маска.
- `harness` — харнес, только для которого модуль существует. Без поля модуль
  ставится во все.
- `copies` — сколько копий ставит установщик. Только у хуков, по умолчанию одна.

TOML взят потому, что его читает стандартная библиотека Python 3.11
(`tomllib`), а зависимостей у нас нет. Данных, режима, вида и привязки в шапке
нет: вид модуля — это состав его папки.

Семейство — общее имя группы модулей, маска — имя семейства и `-*`. Адаптер в
`for` и зависимый в `requires` пишут одну и ту же маску, поэтому требование
совпадает с модулем по slug (голым или под маску) и с адаптером, у которого в
`for` стоит то же требование. Обратный ход — «адаптеры базы»: база называет свой
slug, а адаптер подходит, когда какая-то строка его `for` совпадает с этим
slug'ом точно или накрывает его маской.
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
FIELDS = ('slug', 'for', 'events', 'requires', 'harness', 'copies')
FAMILY_SUFFIX = '-*'
# Копия получает slug с номером на конце; номер копия читает из своего slug.
COPY_SLUG = re.compile(r'^(?P<base>.+)-(?P<index>[1-9][0-9]*)$')


@dataclass(frozen=True)
class Manifest:
    """Прочитанная шапка. `path` — папка модуля, а не файл шапки."""

    slug: str
    events: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    serves: tuple[str, ...] = ()  # поле `for`: `for` — ключевое слово Python
    harness: str | None = None
    copies: int = 1
    path: Path | None = None


def _string_list(raw: Mapping, key: str, where: str, bare_string: bool = False) -> tuple[str, ...]:
    """Список строк. У `for` одна строка принимается как список из одной."""
    value = raw.get(key, [])
    if bare_string and isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f'{where}: поле {key} должно быть списком строк')
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f'{where}: в {key} должна быть непустая строка, а не {item!r}')
    return tuple(item.strip() for item in value)


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

    harness = raw.get('harness')
    if harness is not None and (not isinstance(harness, str) or not harness.strip()):
        raise ValueError(f'{where}: поле harness либо непустая строка, либо его нет')

    copies = raw.get('copies', 1)
    # `bool` — подкласс `int`, а `copies = true` копией не является.
    if isinstance(copies, bool) or not isinstance(copies, int) or copies < 1:
        raise ValueError(f'{where}: поле copies — целое не меньше единицы, а не {copies!r}')
    if 'copies' in raw and not events:
        raise ValueError(
            f'{where}: поле copies есть только у хуков, а events у модуля пусто: '
            'копировать нечего — строки хука у модуля нет'
        )

    return Manifest(
        slug=slug,
        events=events,
        requires=_string_list(raw, 'requires', where),
        serves=_string_list(raw, 'for', where, bare_string=True),
        harness=harness.strip() if isinstance(harness, str) else None,
        copies=copies,
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


def is_family_mask(requirement: str) -> bool:
    return requirement.endswith(FAMILY_SUFFIX)


def matches(requirement: str, manifest: Manifest) -> bool:
    """Отвечает ли модуль на это требование.

    Голый slug — по slug; маска семейства — по slug любого модуля семейства и
    по адаптеру, у которого в `for` записано то же требование.
    """
    if requirement == manifest.slug:
        return True
    if requirement in manifest.serves:
        return True
    return is_family_mask(requirement) and fnmatch.fnmatchcase(manifest.slug, requirement)


def serves_slug(entry: str, slug: str) -> bool:
    """Накрывает ли строка `for` этот slug: точно или маской семейства."""
    if entry == slug:
        return True
    return is_family_mask(entry) and fnmatch.fnmatchcase(slug, entry)


def adapters_of(slug: str, manifests: Iterable[Manifest]) -> list[Manifest]:
    """Адаптеры базы: соседи, чей `for` совпадает с её slug'ом точно или маской.

    Сама база в список не попадает: `for` у неё нет, а был бы — она служила бы
    себе. Порядок — по slug: склейка поставщиков обязана быть повторяемой.
    """
    found = [
        manifest
        for manifest in manifests
        if manifest.slug != slug and any(serves_slug(entry, slug) for entry in manifest.serves)
    ]
    return sorted(found, key=lambda manifest: manifest.slug)


def copy_slug(slug: str, index: int) -> str:
    """Slug копии: номер на конце, счёт с единицы."""
    return f'{slug}-{index}'


def copy_index(slug: str) -> int:
    """Номер копии из её slug'а. Номера нет — копия единственная, номер 1."""
    found = COPY_SLUG.match(slug)
    return int(found.group('index')) if found else 1


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
