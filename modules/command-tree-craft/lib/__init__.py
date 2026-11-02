"""Разбор команд Craft MCP для соседей по slug.

Пакет из модулей по предмету, а здесь только их публичные имена: замок
типов и поставщик ревью зовут `craft.find_tags`, `craft.write_targets` и
остальное отсюда, не зная, в каком модуле что лежит.

- tools — имя инструмента Craft MCP и команда из его входа.
- batch — пачка команд через «;» вне кавычек.
- tags — теги из набора вызывающего вне текста ссылок, `\\uXXXX` раскрыты.
- ids — id блоков в команде и страницы в ответе чтения.
- targets — цели записи: флаги, роль цели, позиция, id из JSON.

Модуль чистый: ни сети, ни файлов, ни типов сущностей. Какие теги искать,
решает тот, кто зовёт. Подмодули грузятся относительным импортом, поэтому
lib грузится пакетом: ядро делает это само (registry.LibraryModule).
"""

from .batch import split_commands
from .ids import BLOCK_ID, answer_page_ids, block_ids, is_block_id, unique_ids
from .tags import TAG_PREFIX, decode_escapes, find_tags, strip_links, tag_pattern
from .targets import Anchor, Role, Targets, is_before, target_ids, verb_of, write_targets
from .tools import (
    CRAFT_READ_TOOL, CRAFT_TOOL_PREFIX, CRAFT_WRITE_TOOLS, command_of, craft_tool, is_craft_read,
    is_craft_write,
)

__all__ = [
    'BLOCK_ID', 'CRAFT_READ_TOOL', 'CRAFT_TOOL_PREFIX', 'CRAFT_WRITE_TOOLS', 'TAG_PREFIX',
    'Anchor', 'Role', 'Targets', 'answer_page_ids', 'block_ids', 'command_of', 'craft_tool',
    'decode_escapes', 'find_tags', 'is_before', 'is_block_id', 'is_craft_read', 'is_craft_write',
    'split_commands', 'strip_links', 'tag_pattern', 'target_ids', 'unique_ids', 'verb_of',
    'write_targets',
]
