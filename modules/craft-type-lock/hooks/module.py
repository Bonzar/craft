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
блоков. Это единственный поход замка в сеть, и только ради записи в Craft.
"""

import fcntl
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
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
# Страницы, которые агент читал в сессии, свежие первыми. Среди них замок
# ищет цель записи решения: connect-API родителя блока не отдаёт, а
# прочитанная страница по правилу чтения до записи у цели есть всегда.
READS_KEY = 'reads'
MAX_READS = 8

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
BASE_ENV = 'CRAFT_API_BASE'
# Без User-Agent connect-API отвечает 403 (замер 01.11.2026 из контейнера).
USER_AGENT = 'jarvis-craft-type-lock'
API_TIMEOUT_SEC = 10
# Решения этапа лежат внутри блока этапа, а этап агент читает со страницы
# проекта на глубину 2: глубже цель записи решения не бывает.
API_DEPTH = 2
BLOCK_ID = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')
ANCHOR_FLAG = re.compile(r'--(id|ids|siblingId|targetId|targetSiblingId)\s+(\S+)')
POSITION_FLAG = re.compile(r'--position\s+(\S+)')
POSITION_BEFORE = 'before'
ADD_COMMAND = 'blocks add'
CONTENT_KEY = 'content'
LEVEL_KEY = 'indentationLevel'
LIST_STYLE_KEY = 'listStyle'
TOGGLE_STYLE = 'toggle'
MARKDOWN_KEY = 'markdown'
DECISION_TOGGLE = re.compile(r'^[\s+#]*Решения(\s+(проекта|сферы))?\s*$')


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


def block_ids(text: str) -> list[str]:
    """Id блоков из текста, без повторов, в порядке появления."""
    return list(dict.fromkeys(match.group(0).lower() for match in BLOCK_ID.finditer(text)))


def new_state(turn: int = 0, skills: dict | None = None, reads: list | None = None) -> dict:
    return {TURN_KEY: turn, SKILLS_KEY: dict(skills or {}), READS_KEY: list(reads or [])}


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
    if not isinstance(turn, int) or not isinstance(skills, dict) or not isinstance(reads, list):
        raise ValueError(f'файл состояния замка {path} испорчен: {state!r}. '
                         'Удали его, счёт начнётся заново')
    return new_state(turn, skills, reads)


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
    return list(dict.fromkeys([*fresh, *reads]))[:MAX_READS]


# --- место записи: под тогглом «Решения» или нет ---------------------------

def is_decision_toggle(block: dict) -> bool:
    markdown = block.get(MARKDOWN_KEY) if isinstance(block, dict) else None
    return (isinstance(block, dict) and block.get(LIST_STYLE_KEY) == TOGGLE_STYLE
            and isinstance(markdown, str) and DECISION_TOGGLE.match(markdown) is not None)


def level_of(block: dict) -> int:
    level = block.get(LEVEL_KEY) if isinstance(block, dict) else None
    return level if isinstance(level, int) else 0


def under_decisions(siblings: list, index: int) -> bool:
    """Лежит ли блок siblings[index] под тогглом «Решения».

    Решения стоят соседями тоггла на +1 от него. Идём назад до первого блока
    без отступа: это сам тоггл, если блок под ним, иначе заголовок другой
    секции или разделитель.
    """
    for position in range(index, -1, -1):
        block = siblings[position]
        if is_decision_toggle(block):
            return True
        if level_of(block) == 0:
            return False
    return False


def find_block(tree: dict, target: str) -> tuple[list, int] | None:
    """Соседи и индекс блока target внутри дерева страницы; нет — None."""
    children = tree.get(CONTENT_KEY) if isinstance(tree, dict) else None
    if not isinstance(children, list):
        return None
    for index, child in enumerate(children):
        if isinstance(child, dict) and str(child.get('id', '')).lower() == target:
            return children, index
    for child in children:
        found = find_block(child, target)
        if found is not None:
            return found
    return None


def landing(command: str, tree: dict, flag: str, anchor: str) -> tuple[list, int] | None:
    """Соседи и индекс того блока, рядом с которым окажется запись.

    Сосед по --siblingId: сама запись встаёт после него, при --position
    before — перед ним, то есть рядом с предыдущим блоком. Родитель по --id у
    `blocks add` — запись встаёт в конец его детей. Любой другой --id —
    правка самого блока.
    """
    if str(tree.get('id', '')).lower() == anchor:
        siblings, index = [tree], 0
    else:
        found = find_block(tree, anchor)
        if found is None:
            return None
        siblings, index = found
    position = POSITION_FLAG.search(command)
    before = position is not None and position.group(1) == POSITION_BEFORE
    if flag in ('siblingId', 'targetSiblingId'):
        return (siblings, index - 1) if before and index > 0 else (siblings, index)
    if command.lstrip().startswith(ADD_COMMAND):
        children = siblings[index].get(CONTENT_KEY)
        if isinstance(children, list) and children:
            return children, 0 if before else len(children) - 1
    return siblings, index


def fetch_tree(base: str, page_id: str) -> dict:
    query = urllib.parse.urlencode({'id': page_id, 'maxDepth': API_DEPTH})
    request = urllib.request.Request(
        f'{base.rstrip("/")}/blocks?{query}',
        headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=API_TIMEOUT_SEC) as response:
        tree = json.load(response)
    if isinstance(tree, list):
        tree = tree[0] if tree else {}
    if not isinstance(tree, dict):
        raise ValueError('ответ API не блок')
    return tree


def decision_zone(command: str, reads: list[str], base: str | None) -> tuple[bool | None, str]:
    """Ложится ли запись под «Решения»: True, False или None с причиной.

    None — замок судить не может: API недоступен, цель не нашлась среди
    прочитанных страниц. Тогда он молчит с причиной в следе: лишний запрет
    остановил бы верную запись, а запись без скилла поймает ревью хода.
    """
    anchors = [(flag, value.lower()) for flag, raw in ANCHOR_FLAG.findall(command)
               for value in raw.split(',') if BLOCK_ID.fullmatch(value)]
    if not anchors:
        return False, ''
    if not base:
        return None, f'{BASE_ENV} не задан, место записи не проверить'
    if not reads:
        return None, 'в сессии нет чтений Craft, место записи не проверить'
    failures = []
    for page_id in reads:
        try:
            tree = fetch_tree(base, page_id)
        except (urllib.error.URLError, OSError, ValueError) as error:
            failures.append(f'{page_id[:8]}: {error}')
            continue
        for flag, anchor in anchors:
            place = landing(command, tree, flag, anchor)
            if place is None:
                continue
            siblings, index = place
            if index < 0:
                return False, f'цель {anchor[:8]} стоит первой, записи «Решений» выше нет'
            if under_decisions(siblings, index):
                return True, f'цель {anchor[:8]} под тогглом «Решения» на странице {page_id[:8]}'
            return False, f'цель {anchor[:8]} не под «Решениями» на странице {page_id[:8]}'
    if failures:
        return None, 'API Craft не ответил: ' + '; '.join(failures)
    return None, 'цель записи не найдена среди страниц, прочитанных в сессии'


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
            storage.write_json(STATE_FILE, new_state(turn, state[SKILLS_KEY], state[READS_KEY]))
        return jarvis.Silence(reason=f'начался ход {turn}')

    def on_after_compact(self, event, storage) -> jarvis.Response:
        # После сжатия правила скилла из контекста ушли: вызывать заново.
        # Чтения остаются: это не знание агента, а подсказка, где искать цель.
        with state_lock(storage):
            state = read_state(storage)
            storage.write_json(STATE_FILE, new_state(reads=state[READS_KEY]))
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
                turn, {**state[SKILLS_KEY], slug: turn}, state[READS_KEY]))
        return jarvis.Silence(reason=f'скилл {slug} вызван в ходе {turn}')

    def on_read(self, event, storage) -> jarvis.Response:
        ids = block_ids(command_of(event.tool_input))
        if not ids:
            return jarvis.Silence(reason='чтение без id блока')
        with state_lock(storage):
            state = read_state(storage)
            storage.write_json(STATE_FILE, new_state(
                state[TURN_KEY], state[SKILLS_KEY], remember_reads(state[READS_KEY], ids)))
        return jarvis.Silence(reason=f'прочитано: {", ".join(item[:8] for item in ids)}')

    def on_pre_tool(self, event, storage) -> jarvis.Response:
        if not is_craft_write(event.tool_name):
            return jarvis.Silence(reason='вызов не пишет в Craft')
        command = command_of(event.tool_input)
        if not command.strip():
            return jarvis.Silence(reason='в вызове нет команды')
        tag_skills = load_tag_skills()
        tags = find_tags(command, tag_pattern(tag_skills))
        state = read_state(storage)
        decision, note = decision_zone(command, state[READS_KEY], os.environ.get(BASE_ENV))
        needed = sorted({tag_skills[tag] for tag in tags} | ({DECISION_SKILL} if decision else set()))
        tail = f', {note}' if note else ''
        if not needed:
            return jarvis.Silence(reason=f'тега типа нет{tail}')
        missing = [slug for slug in needed if state[SKILLS_KEY].get(slug) != state[TURN_KEY]]
        if missing:
            return jarvis.Deny(reason=deny_reason(tag_skills, tags, missing, bool(decision)))
        found = f'теги {", ".join(tags)}: ' if tags else ''
        return jarvis.Silence(reason=f'{found}скиллы вызваны в этом ходе{tail}')


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
