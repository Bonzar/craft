#!/usr/bin/env python3
"""Замок «запись в тип сущности без его скилла».

Агент не запишет в Craft сущность с тегом типа (#задача, #этап и другие),
пока в этом ходе не вызвал скилл этого типа. Скилл несёт правила типа, и
вызванный в начале сессии к концу длинного хода уже забыт — поэтому счёт
идёт по ходам, а не по сессии.

Решение тега не несёт: его узнаём по месту. Запись, которая ложится под
тоггл «Решения», «Решения проекта» или «Решения сферы», требует скилла
craft-decision. Место замок берёт из connect-API Craft: страницы, которые
агент читал в сессии, запрашиваются заново, и цель записи ищется среди их
блоков. Цели нет ни на одной — запись в непрочитанное, и она тоже под
запретом. API недоступен — замок молчит с причиной в следе. Это единственный
поход замка в сеть, и только ради записи в Craft.
"""

import fcntl
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jarvis  # noqa: E402
from jarvis import events, wrappers  # noqa: E402
from place import BASE_ENV, BLOCK_ID, Verdict, decision_zone  # noqa: E402

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
# Страницы, которые агент читал в сессии, свежие первыми. Среди них замок
# ищет цель записи: connect-API родителя блока не отдаёт, а по правилу
# чтения до записи страница цели среди прочитанных должна быть.
READS_KEY = 'reads'
MAX_READS = 20
# Предупреждение о непроверенном месте записи: раз за сессию, текстом в ход.
# stderr хука до модели не доходит (замер 01.11.2026 живой сессией).
WARNED_KEY = 'place_warned'
WARNING_PREFIX = 'Замок craft-type-lock: место записи в Craft не проверяется,'
# Страница из ответа чтения: `blocks get --date today` id в команде не несёт,
# а писать в заметку дня агент потом будет по id из ответа.
PAGE_TAG = re.compile(r'<page id="([^"]+)"')
MAX_RESULT_PAGES = 4

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
CRAFT_READ_TOOL = 'craft_read'
COMMAND_FIELD = 'command'
# Агент может прислать JSON-полезную нагрузку с кириллицей в виде \uXXXX:
# тег там тот же, и пропускать его нельзя.
UNICODE_ESCAPE = re.compile(r'\\u([0-9a-fA-F]{4})')
# Текст ссылки — копия имени цели вместе с её тегами. Ссылка на этап из
# задачи этап не пишет, поэтому тег в тексте ссылки не считается.
BLOCK_LINK = re.compile(r'\[[^\]]*\]\((?:block|craftdocs)://[^)]*\)')

DECISION_SKILL = 'craft-decision'
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


def craft_tool(tool_name) -> str:
    if not isinstance(tool_name, str) or not tool_name.startswith(MCP_PREFIX):
        return ''
    return tool_name.rsplit(MCP_NAME_SEPARATOR, 1)[-1]


def is_craft_write(tool_name) -> bool:
    return craft_tool(tool_name) in CRAFT_WRITE_TOOLS


def skill_slug(tool_input) -> str:
    name = tool_input.get(SKILL_INPUT_FIELD) if isinstance(tool_input, dict) else None
    if not isinstance(name, str):
        return ''
    return name.rsplit(PLUGIN_SEPARATOR, 1)[-1].strip()


def command_of(tool_input) -> str:
    command = tool_input.get(COMMAND_FIELD) if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else ''


def unique_ids(ids) -> list[str]:
    """Без повторов в любом регистре, порядок и регистр первого вхождения."""
    seen, result = set(), []
    for block_id in ids:
        if block_id.lower() not in seen:
            seen.add(block_id.lower())
            result.append(block_id)
    return result


def block_ids(text: str) -> list[str]:
    """Id блоков из текста, без повторов, в порядке появления."""
    return unique_ids(match.group(0) for match in BLOCK_ID.finditer(text))


def new_state(turn: int = 0, skills: dict | None = None, reads: list | None = None,
              warned: bool = False) -> dict:
    return {TURN_KEY: turn, SKILLS_KEY: dict(skills or {}), READS_KEY: list(reads or []),
            WARNED_KEY: warned}


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
    reads = state.get(READS_KEY, []) if isinstance(state, dict) else None
    if (type(turn) is not int or not isinstance(skills, dict) or not isinstance(reads, list)
            or not all(isinstance(item, str) and BLOCK_ID.fullmatch(item) for item in reads)):
        raise ValueError(f'файл состояния замка {path} испорчен: {state!r}. '
                         'Удали его, счёт начнётся заново')
    return new_state(turn, skills, reads, bool(state.get(WARNED_KEY)))


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


def remember_reads(reads: list[str], fresh: list[str]) -> list[str]:
    """Свежие чтения вперёд, без повторов, не длиннее MAX_READS."""
    return unique_ids([*fresh, *reads])[:MAX_READS]


def deny_reason(tag_skills: dict[str, str], tags: list[str], missing: list[str],
                decision: bool) -> str:
    reasons = []
    if tags:
        pairs = ', '.join(f'{tag} → {tag_skills[tag]}' for tag in tags)
        reasons.append(f'В записи есть тег типа: {pairs}.')
    if decision:
        reasons.append(f'Запись ложится под тоггл «Решения»: это решение, его скилл {DECISION_SKILL}.')
    return (' '.join(reasons) + f' В этом ходе не вызван скилл: {", ".join(missing)}. '
            f'Вызови его инструментом {SKILL_TOOL}, прочитай правила типа и повтори запись.')


def unread_reason(note: str) -> str:
    return (f'Запись в непрочитанное: {note}. Прочитай страницу, куда пишешь, '
            '«blocks get ‹id страницы› --depth -1», и повтори запись.')


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
            storage.write_json(STATE_FILE, new_state(turn, state[SKILLS_KEY], state[READS_KEY],
                                                     state[WARNED_KEY]))
        return jarvis.Silence(reason=f'начался ход {turn}')

    def on_after_compact(self, event, storage) -> jarvis.Response:
        # После сжатия правила скилла из контекста ушли: вызывать заново.
        # Чтения остаются: это не знание агента, а подсказка, где искать цель.
        with state_lock(storage):
            state = read_state(storage)
            storage.write_json(STATE_FILE, new_state(reads=state[READS_KEY], warned=state[WARNED_KEY]))
        return jarvis.Silence(reason='после сжатия счёт ходов и скиллов сброшен')

    def on_post_tool(self, event, storage) -> jarvis.Response:
        if craft_tool(event.tool_name) == CRAFT_READ_TOOL:
            return self.on_read(event, storage)
        if event.tool_name != SKILL_TOOL:
            return jarvis.Silence(reason='вызван не скилл')
        slug = skill_slug(event.tool_input)
        if slug not in {*load_tag_skills().values(), DECISION_SKILL}:
            return jarvis.Silence(reason=f'скилл {slug or "без имени"} не скилл типа')
        with state_lock(storage):
            state = read_state(storage)
            turn = state[TURN_KEY]
            storage.write_json(STATE_FILE, new_state(
                turn, {**state[SKILLS_KEY], slug: turn}, state[READS_KEY], state[WARNED_KEY]))
        return jarvis.Silence(reason=f'скилл {slug} вызван в ходе {turn}')

    def on_read(self, event, storage) -> jarvis.Response:
        pages = [page for page in PAGE_TAG.findall(str(event.tool_result or ''))
                 if BLOCK_ID.fullmatch(page)][:MAX_RESULT_PAGES]
        ids = unique_ids([*block_ids(command_of(event.tool_input)), *pages])
        if not ids:
            return jarvis.Silence(reason='чтение без id блока')
        with state_lock(storage):
            state = read_state(storage)
            storage.write_json(STATE_FILE, new_state(
                state[TURN_KEY], state[SKILLS_KEY], remember_reads(state[READS_KEY], ids),
                state[WARNED_KEY]))
        return jarvis.Silence(reason=f'прочитано: {", ".join(item[:8] for item in ids)}')

    def warn_once(self, storage, state: dict, reason: str) -> str | None:
        """Текст в ход о том, что место записи не проверяется; раз за сессию.

        Молчаливый отказ от проверки на всю сессию прятал бы решения без
        скилла; одного предупреждения хватает, повторять на каждой записи
        незачем. Второй и дальше — None.
        """
        if state[WARNED_KEY]:
            return None
        with state_lock(storage):
            fresh = read_state(storage)
            storage.write_json(STATE_FILE, {**fresh, WARNED_KEY: True})
        return f'{WARNING_PREFIX} {reason}. Решение без craft-decision поймает только ревью хода.'

    def on_pre_tool(self, event, storage) -> jarvis.Response:
        if not is_craft_write(event.tool_name):
            return jarvis.Silence(reason='вызов не пишет в Craft')
        command = command_of(event.tool_input)
        if not command.strip():
            return jarvis.Silence(reason='в вызове нет команды')
        tag_skills = load_tag_skills()
        tags = find_tags(command, tag_pattern(tag_skills))
        state = read_state(storage)
        place = decision_zone(command, state[READS_KEY], os.environ.get(BASE_ENV))
        if place.verdict is Verdict.UNREAD:
            return jarvis.Deny(reason=unread_reason(place.reason))
        warning = None
        if place.verdict is Verdict.UNKNOWN:
            tail = f', место записи не проверено: {place.reason}'
            if place.outage:
                warning = self.warn_once(storage, state, place.reason)
        elif place.verdict in (Verdict.DECISION, Verdict.OTHER):
            tail = f', {place.reason}' if place.reason else ''
        else:
            raise ValueError(f'неизвестный исход проверки места: {place.verdict!r}')
        decision = place.verdict is Verdict.DECISION
        needed = sorted({tag_skills[tag] for tag in tags} | ({DECISION_SKILL} if decision else set()))
        if not needed:
            return self.pass_through(f'тега типа нет{tail}', warning)
        missing = [slug for slug in needed if state[SKILLS_KEY].get(slug) != state[TURN_KEY]]
        if missing:
            return jarvis.Deny(reason=deny_reason(tag_skills, tags, missing, decision))
        found = f'теги {", ".join(tags)}: ' if tags else ''
        return self.pass_through(f'{found}скиллы вызваны в этом ходе{tail}', warning)

    @staticmethod
    def pass_through(reason: str, warning: str | None) -> jarvis.Response:
        """Пропустить запись: молча, либо с предупреждением текстом в ход."""
        return jarvis.Context(text=warning) if warning else jarvis.Silence(reason=reason)


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
