#!/usr/bin/env python3
"""Замок «запись в тип сущности без его скилла».

Агент не запишет в Craft сущность с тегом типа (#задача, #этап и другие),
пока в этом ходе не вызвал скилл этого типа. Скилл несёт правила типа, и
вызванный в начале сессии к концу длинного хода уже забыт — поэтому счёт
идёт по ходам, а не по сессии.

Замок ходит только по своему журналу в хранилище и по входу вызова: в Craft
и в сеть не ходит. На верной записи он молчит.
"""

import fcntl
import json
import re
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import events, wrappers  # noqa: E402

TYPES_FILE = Path(__file__).resolve().parents[1] / 'data' / 'types.json'
TYPES_KEY = 'types'
TYPE_SKILL_KEY = 'skill'
TAG_PREFIX = '#'

STATE_FILE = 'craft-type-lock.json'
# Хуки параллельных вызовов идут параллельно: два скилла в одном сообщении
# без лока перезаписали бы друг друга, и один пропал бы из счёта.
LOCK_FILE = 'craft-type-lock.lock'
TURN_KEY = 'turn'
SKILLS_KEY = 'skills'

SKILL_TOOL = 'Skill'
SKILL_INPUT_FIELD = 'skill'
# Плагинный скилл приходит как «плагин:имя»; slug — часть после двоеточия.
PLUGIN_SEPARATOR = ':'
# Имя сервера Craft MCP зависит от того, как его подключили, поэтому
# инструмент узнаём по хвосту имени. write_and_show исполняет те же команды,
# что write: без него замок обходился бы сменой инструмента.
MCP_PREFIX = 'mcp__'
MCP_NAME_SEPARATOR = '__'
CRAFT_WRITE_TOOLS = ('craft_write', 'craft_write_and_show')
COMMAND_FIELD = 'command'
# Агент может прислать JSON-полезную нагрузку с кириллицей в виде \uXXXX:
# тег там тот же, и пропускать его нельзя.
UNICODE_ESCAPE = re.compile(r'\\u([0-9a-fA-F]{4})')
# Текст ссылки — копия имени цели вместе с её тегами. Ссылка на этап из
# задачи этап не пишет, поэтому тег в тексте ссылки не считается.
BLOCK_LINK = re.compile(r'\[[^\]]*\]\((?:block|craftdocs)://[^)]*\)')


def load_tag_skills(path: Path = TYPES_FILE) -> dict[str, str]:
    """Тег → slug скилла."""
    try:
        table = json.loads(path.read_text(encoding='utf-8')).get(TYPES_KEY)
    except (json.JSONDecodeError, AttributeError) as error:
        raise ValueError(f'{path}: справочник типов не читается как объект JSON') from error
    if not isinstance(table, dict) or not table:
        raise ValueError(f'{path}: нет непустого объекта «{TYPES_KEY}»')
    result = {}
    for tag, entry in table.items():
        skill = entry.get(TYPE_SKILL_KEY) if isinstance(entry, dict) else None
        if not tag.startswith(TAG_PREFIX) or not isinstance(skill, str) or not skill:
            raise ValueError(f'{path}: у тега {tag!r} нет скилла')
        result[tag.lower()] = skill
    return result


def tag_pattern(tags) -> re.Pattern:
    """Тег целым словом; хвост через слеш (#задача/готово) тоже тег.

    Регистр не важен: проверено ли, что Craft считает #Задача тем же тегом,
    мы не знаем, а лишний запрет дешевле пропуска.
    """
    names = '|'.join(re.escape(tag[1:]) for tag in sorted(tags, key=len, reverse=True))
    return re.compile(rf'(?<![\w#])#({names})(?![\w-])', re.IGNORECASE)


def find_tags(command: str, pattern: re.Pattern) -> list[str]:
    text = UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), command)
    text = BLOCK_LINK.sub('', text)
    found = {TAG_PREFIX + match.group(1).lower() for match in pattern.finditer(text)}
    return sorted(found)


def is_craft_write(tool_name) -> bool:
    if not isinstance(tool_name, str) or not tool_name.startswith(MCP_PREFIX):
        return False
    return tool_name.rsplit(MCP_NAME_SEPARATOR, 1)[-1] in CRAFT_WRITE_TOOLS


def skill_slug(tool_input) -> str:
    name = tool_input.get(SKILL_INPUT_FIELD) if isinstance(tool_input, dict) else None
    if not isinstance(name, str):
        return ''
    return name.rsplit(PLUGIN_SEPARATOR, 1)[-1].strip()


def new_state(turn: int = 0, skills: dict | None = None) -> dict:
    return {TURN_KEY: turn, SKILLS_KEY: dict(skills or {})}


def read_state(storage: jarvis.Storage) -> dict:
    path = storage.path(STATE_FILE)
    try:
        state = storage.read_json(STATE_FILE, default=None)
    except json.JSONDecodeError as error:
        raise ValueError(f'файл состояния замка {path} не JSON: {error}. '
                         'Удали его, счёт начнётся заново') from error
    if state is None:
        return new_state()
    turn = state.get(TURN_KEY) if isinstance(state, dict) else None
    skills = state.get(SKILLS_KEY) if isinstance(state, dict) else None
    if not isinstance(turn, int) or not isinstance(skills, dict):
        raise ValueError(f'файл состояния замка {path} испорчен: {state!r}. '
                         'Удали его, счёт начнётся заново')
    return new_state(turn, skills)


@contextmanager
def state_lock(storage: jarvis.Storage):
    path = storage.path(LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def deny_reason(tag_skills: dict[str, str], tags: list[str], missing: list[str]) -> str:
    pairs = ', '.join(f'{tag} → {tag_skills[tag]}' for tag in tags)
    return (
        f'В записи есть тег типа: {pairs}. В этом ходе не вызван скилл: '
        f'{", ".join(missing)}. Вызови его инструментом {SKILL_TOOL}, '
        'прочитай правила типа и повтори запись.'
    )


class Module(jarvis.Module):
    """Ход — от реплики до реплики; сжатие обнуляет счёт."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        handlers = {
            events.PROMPT: self.on_prompt,
            events.POST_TOOL: self.on_post_tool,
            events.PRE_TOOL: self.on_pre_tool,
            events.AFTER_COMPACT: self.on_after_compact,
        }
        handler = handlers.get(event.event)
        if handler is None:
            return jarvis.Silence(reason=f'событие {event.event} замку не нужно')
        return handler(event, runtime.storage)

    def on_prompt(self, event, storage) -> jarvis.Response:
        with state_lock(storage):
            state = read_state(storage)
            turn = state[TURN_KEY] + 1
            storage.write_json(STATE_FILE, new_state(turn, state[SKILLS_KEY]))
        return jarvis.Silence(reason=f'начался ход {turn}')

    def on_after_compact(self, event, storage) -> jarvis.Response:
        # После сжатия правила скилла из контекста ушли: вызывать заново.
        with state_lock(storage):
            storage.write_json(STATE_FILE, new_state())
        return jarvis.Silence(reason='после сжатия счёт ходов и скиллов сброшен')

    def on_post_tool(self, event, storage) -> jarvis.Response:
        if event.tool_name != SKILL_TOOL:
            return jarvis.Silence(reason='вызван не скилл')
        slug = skill_slug(event.tool_input)
        if slug not in set(load_tag_skills().values()):
            return jarvis.Silence(reason=f'скилл {slug or "без имени"} не скилл типа')
        with state_lock(storage):
            state = read_state(storage)
            turn = state[TURN_KEY]
            storage.write_json(STATE_FILE, new_state(turn, {**state[SKILLS_KEY], slug: turn}))
        return jarvis.Silence(reason=f'скилл {slug} вызван в ходе {turn}')

    def on_pre_tool(self, event, storage) -> jarvis.Response:
        if not is_craft_write(event.tool_name):
            return jarvis.Silence(reason='вызов не пишет в Craft')
        tool_input = event.tool_input if isinstance(event.tool_input, dict) else {}
        command = tool_input.get(COMMAND_FIELD)
        if not isinstance(command, str) or not command.strip():
            return jarvis.Silence(reason='в вызове нет команды')
        tag_skills = load_tag_skills()
        tags = find_tags(command, tag_pattern(tag_skills))
        if not tags:
            return jarvis.Silence(reason='тега типа нет')
        state = read_state(storage)
        needed = sorted({tag_skills[tag] for tag in tags})
        missing = [slug for slug in needed if state[SKILLS_KEY].get(slug) != state[TURN_KEY]]
        if missing:
            return jarvis.Deny(reason=deny_reason(tag_skills, tags, missing))
        return jarvis.Silence(reason=f'теги {", ".join(tags)}: скиллы вызваны в этом ходе')


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
