"""Справочник «тег типа → скилл и шаблоны» по модулям сущностей.

Часть замка craft-type-lock. Модуль сущности объявляет себя полем `for =
["craft-type-lock", …]` в шапке и несёт `data/entity.json`:

    {"tags": ["#этап", "#веха"],
     "templates": {"#этап": ["‹id шаблона›"], "#веха": ["‹id шаблона›"]}}

Скилл его тегов — скилл самого модуля, имя совпадает со slug по правилу
установщика. Шаблоны — id страниц-шаблонов тега: запись в сущность проходит,
когда в этом ходе прочитан любой из них (регулярная задача делит тег с
обычной, и шаблонов у тега бывает несколько).
Замок находит модули ядром (`registry.adapters`), а не обходом всех папок:
своего справочника у замка нет, отказ от сущности — удаление одного модуля.
"""

import json
import re
from pathlib import Path
from typing import NamedTuple

from jarvis import registry

SLUG = 'craft-type-lock'
ENTITY_FILE = 'entity.json'
ENTITY_TAGS_KEY = 'tags'
TEMPLATES_KEY = 'templates'
DATA_DIR = registry.DATA_PART
TAG_PREFIX = '#'
MIN_TAG_LENGTH = 2
# Id блока Craft, как его разбирает command-tree-craft; регистр не важен.
BLOCK_ID = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')


class TagEntry(NamedTuple):
    """Что требует тег: скилл модуля и шаблоны, любой из которых прочитан."""
    skill: str
    templates: tuple[str, ...]


def read_entity(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f'{path}: модуль объявил for «{SLUG}», а файла сущности нет')
    try:
        entity = json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'{path}: файл сущности не читается как объект JSON') from error
    if not isinstance(entity, dict):
        raise ValueError(f'{path}: файл сущности не читается как объект JSON')
    return entity


def entity_tags(path: Path) -> dict[str, tuple[str, ...]]:
    """Тег → шаблоны одного data/entity.json. Нет файла или он испорчен — ошибка с путём."""
    entity = read_entity(path)
    tags, templates = entity.get(ENTITY_TAGS_KEY), entity.get(TEMPLATES_KEY)
    if not isinstance(tags, list) or not tags:
        raise ValueError(f'{path}: нет непустого списка «{ENTITY_TAGS_KEY}»')
    if not isinstance(templates, dict):
        raise ValueError(f'{path}: нет объекта «{TEMPLATES_KEY}»: тег → id шаблонов')
    found = {}
    for tag in tags:
        if not isinstance(tag, str) or not tag.startswith(TAG_PREFIX) or len(tag) < MIN_TAG_LENGTH:
            raise ValueError(f'{path}: {tag!r} не тег')
        ids = templates.get(tag)
        if (not isinstance(ids, list) or not ids
                or not all(isinstance(item, str) and BLOCK_ID.fullmatch(item) for item in ids)):
            raise ValueError(f'{path}: у тега {tag} в «{TEMPLATES_KEY}» нет непустого списка id шаблонов')
        found[tag] = tuple(ids)
    extra = sorted(set(templates) - set(tags))
    if extra:
        raise ValueError(f'{path}: в «{TEMPLATES_KEY}» шаблоны тегов, которых нет в «{ENTITY_TAGS_KEY}»: '
                         + ', '.join(extra))
    return found


def load_entities(module_dir: Path | str) -> dict[str, TagEntry]:
    """Тег (в нижнем регистре) → скилл и шаблоны.

    module_dir — папка любого модуля каталога: соседи ищутся рядом с ней.
    Модулей сущностей нет — пустой справочник: требовать нечего. Тег у двух
    модулей — ошибка, а не молчаливый выбор.
    """
    result = {}
    for manifest in registry.adapters(module_dir, SLUG):
        path = Path(manifest.path) / DATA_DIR / ENTITY_FILE
        for tag, templates in entity_tags(path).items():
            if tag.lower() in result:
                raise ValueError(f'{path}: тег {tag} уже у модуля {result[tag.lower()].skill}')
            result[tag.lower()] = TagEntry(manifest.slug, templates)
    return result


def load_tag_skills(module_dir: Path | str) -> dict[str, str]:
    """Тег (в нижнем регистре) → slug модуля сущности."""
    return {tag: entry.skill for tag, entry in load_entities(module_dir).items()}


def entity_reason(error: ValueError) -> str:
    return (f'Справочник тегов типа не собирается: {error}. Запись в Craft закрыта, '
            f'пока файл не исправлен: поправь {DATA_DIR}/{ENTITY_FILE} или переустанови набор '
            'из свежего источника, потом повтори запись.')
