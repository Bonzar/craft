#!/usr/bin/env python3
"""Поставщик данных ревью для Craft.

Кладёт запись в объём ревью хода на трёх вызовах Craft MCP и на вызове
скилла: craft_write и craft_write_and_show — запись с тегами типа, craft_read —
чтение с id блоков, Skill — slug скилла. Прочие инструменты Craft в объём не
идут. Вызовы подагента тоже: это рецензент, а не ход. Формат записей —
data/review-records.md.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import registry, wrappers  # noqa: E402

MODULE_DIR = Path(__file__).resolve().parents[1]
REVIEW = 'turn-review'
# Разбор команд Craft: имя инструмента, теги, id блоков.
CRAFT_PARSER = 'command-tree-craft'
SKILL_TOOL = 'Skill'
SKILL_INPUT_FIELD = 'skill'
# Плагинный скилл приходит как «плагин:имя»; slug — часть после двоеточия.
PLUGIN_SEPARATOR = ':'
# Теги сущности: файл data/entity.json её модуля, поле tags. Набор тегов
# поставщик собирает сам и отдаёт разбору команд, который типов не знает.
DATA_DIR = 'data'
ENTITY_FILE = 'entity.json'
ENTITY_TAGS_KEY = 'tags'
TAG_PREFIX = '#'
SUBAGENT_REASON = 'вызов внутри подагента: в объём ревью не идёт'
NOT_OURS_REASON = 'вызов не Craft и не скилл'
OTHER_CRAFT_REASON = ('инструмент Craft {tool} в объём ревью не пишется: пишутся только '
                      'craft_write, craft_write_and_show, craft_read и Skill')


def library(runtime, slug: str):
    found = runtime.library(slug)
    if found is None:
        raise RuntimeError(f'у модуля {slug} нет части lib. Переустанови набор из свежего источника')
    return found.load()


def entity_tags(path: Path) -> list[str]:
    """Теги одного data/entity.json; испорченный файл — ошибка с путём."""
    try:
        tags = json.loads(path.read_text(encoding='utf-8')).get(ENTITY_TAGS_KEY)
    except (json.JSONDecodeError, AttributeError) as error:
        raise ValueError(f'{path}: файл сущности не читается как объект JSON') from error
    if not isinstance(tags, list) or not tags:
        raise ValueError(f'{path}: нет непустого списка «{ENTITY_TAGS_KEY}»')
    for tag in tags:
        if not isinstance(tag, str) or not tag.startswith(TAG_PREFIX) or len(tag) < 2:
            raise ValueError(f'{path}: {tag!r} не тег')
    return tags


def load_tag_skills(modules_dir: Path | None = None) -> dict[str, str]:
    """Тег → slug модуля сущности по файлам data/entity.json соседей.

    Те же правила, что у замка craft-type-lock: тег у двух модулей и набор без
    единого файла сущности — ошибка, а не молчаливый выбор.
    """
    root = Path(modules_dir) if modules_dir is not None else registry.modules_dir(MODULE_DIR)
    result = {}
    for child in sorted(root.iterdir()):
        path = child / DATA_DIR / ENTITY_FILE
        if not child.is_dir() or not path.is_file():
            continue
        for tag in entity_tags(path):
            if tag.lower() in result:
                raise ValueError(f'{path}: тег {tag} уже у модуля {result[tag.lower()]}')
            result[tag.lower()] = child.name
    if not result:
        raise ValueError(f'{root}: ни у одного модуля нет {DATA_DIR}/{ENTITY_FILE}')
    return result


def skill_slug(tool_input) -> str:
    """Slug скилла из входа инструмента Skill; префикс плагина отрезан."""
    name = tool_input.get(SKILL_INPUT_FIELD) if isinstance(tool_input, dict) else None
    if not isinstance(name, str):
        return ''
    return name.rsplit(PLUGIN_SEPARATOR, 1)[-1].strip()


def as_is(value):
    return value if isinstance(value, (str, dict, list)) else None


def write_record(event, review, craft):
    """Запись write и ошибка справочника тегов, если он не собрался.

    Без справочника теги неизвестны: `types` равен null, текст ошибки лежит в
    `types_error`. Запись всё равно ложится, иначе ушла бы от ревью совсем.
    Ловится любая ошибка, а не только порча файла: каталог модулей мог и не
    прочитаться (права, нет каталога).
    """
    command = craft.command_of(event.tool_input)
    fields = {'tool': event.tool_name, 'command': command, 'result': as_is(event.tool_result)}
    try:
        found = craft.find_tags(command, load_tag_skills())
    except Exception as error:  # noqa: BLE001 — запись ложится, ошибка поднимается в handle
        return review.make_write_record(**fields, types=None, types_error=str(error)), error
    return review.make_write_record(**fields, types=found), None


def record_of(event, review, craft):
    """Запись объёма без номера хода и ошибка справочника; не наш вызов — None.

    Номер хода ставит append_turn_record. Id чтения снимаются с повторов с
    учётом регистра: id, набранный в двух регистрах, ревью видит дважды.
    """
    if craft.is_craft_write(event.tool_name):
        return write_record(event, review, craft)
    if craft.is_craft_read(event.tool_name):
        command = craft.command_of(event.tool_input)
        return review.make_read_record(
            command=command, ids=list(dict.fromkeys(craft.BLOCK_ID.findall(command)))), None
    if event.tool_name == SKILL_TOOL:
        return review.make_skill_record(skill=skill_slug(event.tool_input)), None
    return None, None


class Module(jarvis.Module):
    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        review = library(runtime, REVIEW)
        craft = library(runtime, CRAFT_PARSER)
        record, types_error = record_of(event, review, craft)
        if record is None:
            tool = craft.craft_tool(event.tool_name)
            if tool.startswith(craft.CRAFT_TOOL_PREFIX):
                return jarvis.Silence(reason=OTHER_CRAFT_REASON.format(tool=tool))
            return jarvis.Silence(reason=NOT_OURS_REASON)
        try:
            review.append_turn_record(runtime.storage, record)
        finally:
            # Запись уже в объёме, теперь ошибка справочника. Упало и состояние
            # ревью — его ошибка остаётся в цепочке исключения.
            if types_error is not None:
                raise types_error
        return jarvis.Silence(reason=f'в объём ревью: {record.kind}')


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
