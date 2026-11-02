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
запретом. API недоступен — место не судится, проверка по тегам идёт как
обычно, а о том, что место не проверено, замок раз за сессию говорит
текстом в ход, дальше молчит с причиной в следе. Это единственный поход
замка в сеть, и только ради записи в Craft.
"""

import fcntl
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jarvis  # noqa: E402
from jarvis import events, registry, wrappers  # noqa: E402
from place import BASE_ENV, Verdict, decision_zone  # noqa: E402

MODULE_DIR = Path(__file__).resolve().parents[1]
# Разбор команд Craft: теги, id, цели записи, имена инструментов.
CRAFT_PARSER = 'command-tree-craft'
# Теги сущности лежат в данных её модуля: файл data/entity.json с полем
# tags. Замок собирает справочник «тег → скилл» по соседям и чужих модулей
# не знает: отказ от сущности это удаление одного модуля.
ENTITY_FILE = 'entity.json'
ENTITY_TAGS_KEY = 'tags'
DATA_DIR = 'data'
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
# Страницы из ответа чтения: `blocks get --date today` id в команде не несёт,
# а писать в заметку дня агент потом будет по id из ответа. Id из ответа
# идут впереди id из команды: ответ несёт id в том регистре, в каком его
# знает API, а в команде агент мог набрать его иначе.
MAX_RESULT_PAGES = 4

SKILL_TOOL = 'Skill'
SKILL_INPUT_FIELD = 'skill'
# Плагинный скилл приходит как «плагин:имя»; slug — часть после двоеточия.
PLUGIN_SEPARATOR = ':'

DECISION_SKILL = 'craft-decision'


def load_tag_skills(modules_dir: Path | None = None) -> dict[str, str]:
    """Тег → slug модуля сущности по файлам data/entity.json соседей."""
    root = Path(modules_dir) if modules_dir is not None else registry.modules_dir(MODULE_DIR)
    result = {}
    for child in sorted(root.iterdir()):
        path = child / DATA_DIR / ENTITY_FILE
        if not child.is_dir() or not path.is_file():
            continue
        try:
            tags = json.loads(path.read_text(encoding='utf-8')).get(ENTITY_TAGS_KEY)
        except (json.JSONDecodeError, AttributeError) as error:
            raise ValueError(f'{path}: файл сущности не читается как объект JSON') from error
        if not isinstance(tags, list) or not tags:
            raise ValueError(f'{path}: нет непустого списка «{ENTITY_TAGS_KEY}»')
        for tag in tags:
            if not isinstance(tag, str) or not tag.startswith(TAG_PREFIX) or len(tag) < 2:
                raise ValueError(f'{path}: {tag!r} не тег')
            if tag.lower() in result:
                raise ValueError(f'{path}: тег {tag} уже у модуля {result[tag.lower()]}')
            result[tag.lower()] = child.name
    if not result:
        raise ValueError(f'{root}: ни у одного модуля нет data/{ENTITY_FILE}')
    return result


def skill_slug(tool_input) -> str:
    name = tool_input.get(SKILL_INPUT_FIELD) if isinstance(tool_input, dict) else None
    if not isinstance(name, str):
        return ''
    return name.rsplit(PLUGIN_SEPARATOR, 1)[-1].strip()


def new_state(turn: int = 0, skills: dict | None = None, reads: list | None = None,
              warned: bool = False) -> dict:
    return {TURN_KEY: turn, SKILLS_KEY: dict(skills or {}), READS_KEY: list(reads or []),
            WARNED_KEY: warned}


def library(runtime: jarvis.Runtime, slug: str):
    """Часть lib соседа из requires. Нет её — ошибка с советом переустановить."""
    found = runtime.library(slug)
    if found is None:
        raise RuntimeError(f'у модуля {slug} нет части lib. Переустанови набор из свежего источника')
    return found.load()


def read_state(storage: jarvis.Storage, craft) -> dict:
    """Состояние замка; craft — разбор команд Craft, им проверяются id чтений."""
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
            or not all(craft.is_block_id(item) for item in reads)):
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


def remember_reads(reads: list[str], fresh: list[str], craft) -> list[str]:
    """Свежие чтения вперёд, без повторов, не длиннее MAX_READS."""
    return craft.unique_ids([*fresh, *reads])[:MAX_READS]


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


def entity_reason(error: ValueError) -> str:
    return (f'Справочник тегов типа не собирается: {error}. Запись в Craft закрыта, '
            f'пока файл не исправлен: поправь {DATA_DIR}/{ENTITY_FILE} или переустанови набор '
            'из свежего источника, потом повтори запись.')


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
        return handler(event, runtime.storage, library(runtime, CRAFT_PARSER))

    def on_prompt(self, event, storage, craft) -> jarvis.Response:
        with state_lock(storage):
            state = read_state(storage, craft)
            turn = state[TURN_KEY] + 1
            storage.write_json(STATE_FILE, new_state(turn, state[SKILLS_KEY], state[READS_KEY],
                                                     state[WARNED_KEY]))
        return jarvis.Silence(reason=f'начался ход {turn}')

    def on_after_compact(self, event, storage, craft) -> jarvis.Response:
        # После сжатия правила скилла из контекста ушли: вызывать заново.
        # Чтения остаются: это не знание агента, а подсказка, где искать цель.
        with state_lock(storage):
            state = read_state(storage, craft)
            storage.write_json(STATE_FILE, new_state(reads=state[READS_KEY], warned=state[WARNED_KEY]))
        return jarvis.Silence(reason='после сжатия счёт ходов и скиллов сброшен')

    def on_post_tool(self, event, storage, craft) -> jarvis.Response:
        if craft.is_craft_read(event.tool_name):
            return self.on_read(event, storage, craft)
        if event.tool_name != SKILL_TOOL:
            return jarvis.Silence(reason='вызван не скилл')
        slug = skill_slug(event.tool_input)
        if slug not in {*load_tag_skills().values(), DECISION_SKILL}:
            return jarvis.Silence(reason=f'скилл {slug or "без имени"} не скилл типа')
        with state_lock(storage):
            state = read_state(storage, craft)
            turn = state[TURN_KEY]
            storage.write_json(STATE_FILE, new_state(
                turn, {**state[SKILLS_KEY], slug: turn}, state[READS_KEY], state[WARNED_KEY]))
        return jarvis.Silence(reason=f'скилл {slug} вызван в ходе {turn}')

    def on_read(self, event, storage, craft) -> jarvis.Response:
        pages = craft.answer_page_ids(str(event.tool_result or ''))[:MAX_RESULT_PAGES]
        ids = craft.unique_ids([*pages, *craft.block_ids(craft.command_of(event.tool_input))])
        if not ids:
            return jarvis.Silence(reason='чтение без id блока')
        with state_lock(storage):
            state = read_state(storage, craft)
            storage.write_json(STATE_FILE, new_state(
                state[TURN_KEY], state[SKILLS_KEY], remember_reads(state[READS_KEY], ids, craft),
                state[WARNED_KEY]))
        return jarvis.Silence(reason=f'прочитано: {", ".join(item[:8] for item in ids)}')

    def warn_once(self, storage, craft, state: dict, reason: str) -> str | None:
        """Текст в ход о том, что место записи не проверяется; раз за сессию.

        Молчаливый отказ от проверки на всю сессию прятал бы решения без
        скилла; одного предупреждения хватает, повторять на каждой записи
        незачем. Второй и дальше — None.
        """
        if state[WARNED_KEY]:
            return None
        with state_lock(storage):
            fresh = read_state(storage, craft)
            storage.write_json(STATE_FILE, {**fresh, WARNED_KEY: True})
        return f'{WARNING_PREFIX} {reason}. Решение без craft-decision поймает только ревью хода.'

    def on_pre_tool(self, event, storage, craft) -> jarvis.Response:
        if not craft.is_craft_write(event.tool_name):
            return jarvis.Silence(reason='вызов не пишет в Craft')
        command = craft.command_of(event.tool_input)
        if not command.strip():
            return jarvis.Silence(reason='в вызове нет команды')
        try:
            tag_skills = load_tag_skills()
        except ValueError as error:
            # Без справочника тег в команде не опознать. Пропуск отдал бы
            # запись с тегом без скилла типа, поэтому запись закрыта до починки.
            return jarvis.Deny(reason=entity_reason(error))
        tags = craft.find_tags(command, tag_skills)
        state = read_state(storage, craft)
        place = decision_zone(craft.write_targets(command), state[READS_KEY], os.environ.get(BASE_ENV))
        if place.verdict is Verdict.UNREAD:
            return jarvis.Deny(reason=unread_reason(place.reason))
        warning = None
        if place.verdict is Verdict.UNAVAILABLE:
            tail = f', место записи не проверено: {place.reason}'
            warning = self.warn_once(storage, craft, state, place.reason)
        elif place.verdict is Verdict.UNPARSED:
            tail = f', место записи не проверено: {place.reason}'
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
            # Предупреждение не теряется и на запрете: иначе первый отказ API
            # на запрещённой записи съел бы его на всю сессию.
            reason = deny_reason(tag_skills, tags, missing, decision)
            return jarvis.Deny(reason=f'{reason} {warning}' if warning else reason)
        found = f'теги {", ".join(tags)}: ' if tags else ''
        return self.pass_through(f'{found}скиллы вызваны в этом ходе{tail}', warning)

    @staticmethod
    def pass_through(reason: str, warning: str | None) -> jarvis.Response:
        """Пропустить запись: молча с причиной в следе, либо текстом в ход.

        У текста в ход поля причины нет, поэтому причина входит в сам текст.
        """
        return jarvis.Context(text=f'{warning} ({reason})') if warning else jarvis.Silence(reason=reason)


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
