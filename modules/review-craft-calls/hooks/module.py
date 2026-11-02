#!/usr/bin/env python3
"""Поставщик данных ревью для Craft.

На каждом вызове Craft MCP и скилла кладёт запись в объём ревью хода: запись
с тегами типа, чтение с id блоков, вызов скилла. Вызовы подагента в объём не
идут: это рецензент, а не ход. Формат записей — data/review-records.md.
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
COMMAND_FIELD = 'command'
SKILL_TOOL = 'Skill'
HEX = '[0-9a-fA-F]'
BLOCK_ID = re.compile(rf'{HEX}{{8}}-{HEX}{{4}}-{HEX}{{4}}-{HEX}{{4}}-{HEX}{{12}}')
SUBAGENT_REASON = 'вызов внутри подагента: в объём ревью не идёт'
NOT_OURS_REASON = 'вызов не Craft и не скилл'


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


def record_of(event, review, tags) -> dict | None:
    """Запись объёма без номера хода: его ставит append_turn_record."""
    tool = craft_tool(event.tool_name)
    if tool in CRAFT_WRITE_TOOLS:
        command = command_of(event.tool_input)
        return {review.KIND_KEY: review.KIND_WRITE, review.TOOL_KEY: event.tool_name,
                review.COMMAND_KEY: command, review.RESULT_KEY: as_is(event.tool_result),
                review.TYPES_KEY: tags.find_tags(command, tags.load_tag_skills())}
    if tool == CRAFT_READ_TOOL:
        command = command_of(event.tool_input)
        return {review.KIND_KEY: review.KIND_READ, review.COMMAND_KEY: command,
                review.IDS_KEY: list(dict.fromkeys(BLOCK_ID.findall(command)))}
    if event.tool_name == SKILL_TOOL:
        return {review.KIND_KEY: review.KIND_SKILL,
                review.SKILL_KEY: tags.skill_slug(event.tool_input)}
    return None


class Module(jarvis.Module):
    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        review = library(runtime, REVIEW)
        record = record_of(event, review, library(runtime, TYPE_LOCK))
        if record is None:
            return jarvis.Silence(reason=NOT_OURS_REASON)
        review.append_turn_record(runtime.storage, record)
        return jarvis.Silence(reason=f'в объём ревью: {record[review.KIND_KEY]}')


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
