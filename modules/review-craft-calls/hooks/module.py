#!/usr/bin/env python3
"""Поставщик данных ревью для Craft.

Кладёт строку в объём ревью хода на трёх вызовах Craft MCP и на вызове
скилла: craft_write и craft_write_and_show — запись с командой как есть,
craft_read — чтение с id блоков, Skill — slug скилла. Прочие инструменты Craft
в объём не идут. Вызовы подагента тоже: это рецензент, а не ход.

Строки пишутся по контракту поставщиков из README turn-review: словарь с
`kind`, id реплики харнеса `prompt_id` и полями вида, одной строкой JSON через
append_line хранилища ядра. lib ревью модуль не импортирует, теги в команде
не ищет: тип записи кладут строкой entity модули сущностей и замок типов, не он.
Формат строк — data/review-records.md модуля turn-review.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import wrappers  # noqa: E402

# Контракт поставщиков ревью: файл объёма в зоне сессии и id реплики в строке.
VOLUME_FILE = 'turn-review-volume.jsonl'
PROMPT_ID_FIELD = 'prompt_id'
# Разбор команд Craft: имя инструмента, команда, id блоков.
CRAFT_PARSER = 'command-tree-craft'
SKILL_TOOL = 'Skill'
SKILL_INPUT_FIELD = 'skill'
# Плагинный скилл приходит как «плагин:имя»; slug — часть после двоеточия.
PLUGIN_SEPARATOR = ':'
SUBAGENT_REASON = 'вызов внутри подагента: в объём ревью не идёт'
NOT_OURS_REASON = 'вызов не Craft и не скилл'
OTHER_CRAFT_REASON = ('инструмент Craft {tool} в объём ревью не пишется: пишутся только '
                      'craft_write, craft_write_and_show, craft_read и Skill')


def library(runtime, slug: str):
    found = runtime.library(slug)
    if found is None:
        raise RuntimeError(f'у модуля {slug} нет части lib. Переустанови набор из свежего источника')
    return found.load()


def skill_slug(tool_input) -> str:
    """Slug скилла из входа инструмента Skill; префикс плагина отрезан."""
    name = tool_input.get(SKILL_INPUT_FIELD) if isinstance(tool_input, dict) else None
    if not isinstance(name, str):
        return ''
    return name.rsplit(PLUGIN_SEPARATOR, 1)[-1].strip()


def as_is(value):
    return value if isinstance(value, (str, dict, list)) else None


def prompt_id_of(event) -> str | None:
    """Id реплики харнеса из сырого события; нет его или он не строка — None."""
    value = event.raw.get(PROMPT_ID_FIELD)
    return value if isinstance(value, str) and value else None


def volume_row(kind: str, event, **fields) -> dict:
    """Строка объёма по контракту: вид, id реплики (если харнес его дал) и поля вида."""
    prompt_id = prompt_id_of(event)
    head = {'kind': kind} if prompt_id is None else {'kind': kind, PROMPT_ID_FIELD: prompt_id}
    return {**head, **fields}


def row_of(event, craft) -> dict | None:
    """Строка объёма для вызова; не наш вызов — None.

    Id чтения снимаются с повторов с учётом регистра: id, набранный в двух
    регистрах, ревью видит дважды.
    """
    if craft.is_craft_write(event.tool_name):
        return volume_row('write', event, tool=event.tool_name,
                          command=craft.command_of(event.tool_input),
                          result=as_is(event.tool_result))
    if craft.is_craft_read(event.tool_name):
        command = craft.command_of(event.tool_input)
        return volume_row('read', event, command=command,
                          ids=list(dict.fromkeys(craft.BLOCK_ID.findall(command))))
    if event.tool_name == SKILL_TOOL:
        return volume_row('skill', event, skill=skill_slug(event.tool_input))
    return None


class Module(jarvis.Module):
    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        craft = library(runtime, CRAFT_PARSER)
        row = row_of(event, craft)
        if row is None:
            tool = craft.craft_tool(event.tool_name)
            if tool.startswith(craft.CRAFT_TOOL_PREFIX):
                return jarvis.Silence(reason=OTHER_CRAFT_REASON.format(tool=tool))
            return jarvis.Silence(reason=NOT_OURS_REASON)
        runtime.storage.append_line(VOLUME_FILE, json.dumps(row, ensure_ascii=False))
        return jarvis.Silence(reason=f'в объём ревью: {row["kind"]}')


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
