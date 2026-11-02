#!/usr/bin/env python3
"""Поставщик данных ревью для Craft.

Кладёт запись в объём ревью хода на трёх вызовах Craft MCP и на вызове
скилла: craft_write и craft_write_and_show — запись с тегами типа, craft_read —
чтение с id блоков, Skill — slug скилла. Прочие инструменты Craft в объём не
идут. Вызовы подагента тоже: это рецензент, а не ход. Формат записей —
data/review-records.md.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import wrappers  # noqa: E402

REVIEW = 'turn-review'
TYPE_LOCK = 'craft-type-lock'
MCP_PREFIX = 'mcp__'
MCP_NAME_SEPARATOR = '__'
CRAFT_WRITE_TOOLS = ('craft_write', 'craft_write_and_show')
CRAFT_READ_TOOL = 'craft_read'
# Инструмент Craft узнаём по хвосту имени: имя сервера зависит от подключения.
CRAFT_TOOL_PREFIX = 'craft_'
COMMAND_FIELD = 'command'
SKILL_TOOL = 'Skill'
HEX = '[0-9a-fA-F]'
BLOCK_ID = re.compile(rf'{HEX}{{8}}-{HEX}{{4}}-{HEX}{{4}}-{HEX}{{4}}-{HEX}{{12}}')
SUBAGENT_REASON = 'вызов внутри подагента: в объём ревью не идёт'
NOT_OURS_REASON = 'вызов не Craft и не скилл'
OTHER_CRAFT_REASON = ('инструмент Craft {tool} в объём ревью не пишется: пишутся только '
                      'craft_write, craft_write_and_show, craft_read и Skill')


def library(runtime, slug: str):
    found = runtime.library(slug)
    if found is None:
        raise RuntimeError(f'у модуля {slug} нет части lib. Переустанови набор из свежего источника')
    return found.load()


def craft_tool(tool_name) -> str:
    if not isinstance(tool_name, str) or not tool_name.startswith(MCP_PREFIX):
        return ''
    return tool_name.rsplit(MCP_NAME_SEPARATOR, 1)[-1]


def command_of(tool_input) -> str:
    command = tool_input.get(COMMAND_FIELD) if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else ''


def as_is(value):
    return value if isinstance(value, (str, dict, list)) else None


def write_record(event, review, tags) -> tuple[dict, Exception | None]:
    """Запись write и ошибка справочника тегов, если он не собрался.

    Без справочника теги неизвестны: `types` равен null, текст ошибки лежит в
    `types_error`. Запись всё равно ложится, иначе ушла бы от ревью совсем.
    Ловится любая ошибка, а не только порча файла: каталог модулей мог и не
    прочитаться (права, нет каталога).
    """
    command = command_of(event.tool_input)
    record = {review.KIND_KEY: review.KIND_WRITE, review.TOOL_KEY: event.tool_name,
              review.COMMAND_KEY: command, review.RESULT_KEY: as_is(event.tool_result)}
    try:
        return {**record, review.TYPES_KEY: tags.find_tags(command, tags.load_tag_skills())}, None
    except Exception as error:  # noqa: BLE001 — запись ложится, ошибка поднимается в handle
        return {**record, review.TYPES_KEY: None, review.TYPES_ERROR_KEY: str(error)}, error


def record_of(event, review, tags) -> tuple[dict | None, Exception | None]:
    """Запись объёма без номера хода и ошибка справочника; не наш вызов — None.

    Номер хода ставит append_turn_record.
    """
    tool = craft_tool(event.tool_name)
    if tool in CRAFT_WRITE_TOOLS:
        return write_record(event, review, tags)
    if tool == CRAFT_READ_TOOL:
        command = command_of(event.tool_input)
        return {review.KIND_KEY: review.KIND_READ, review.COMMAND_KEY: command,
                review.IDS_KEY: list(dict.fromkeys(BLOCK_ID.findall(command)))}, None
    if event.tool_name == SKILL_TOOL:
        return {review.KIND_KEY: review.KIND_SKILL,
                review.SKILL_KEY: tags.skill_slug(event.tool_input)}, None
    return None, None


class Module(jarvis.Module):
    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        review = library(runtime, REVIEW)
        record, types_error = record_of(event, review, library(runtime, TYPE_LOCK))
        if record is None:
            tool = craft_tool(event.tool_name)
            if tool.startswith(CRAFT_TOOL_PREFIX):
                return jarvis.Silence(reason=OTHER_CRAFT_REASON.format(tool=tool))
            return jarvis.Silence(reason=NOT_OURS_REASON)
        try:
            review.append_turn_record(runtime.storage, record)
        finally:
            # Запись уже в объёме, теперь ошибка справочника. Упало и состояние
            # ревью — его ошибка остаётся в цепочке исключения.
            if types_error is not None:
                raise types_error
        return jarvis.Silence(reason=f'в объём ревью: {record[review.KIND_KEY]}')


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
