#!/usr/bin/env python3
"""Замок «запись в сущность без её скилла».

Агент не запишет в Craft в сущность (задачу, этап, раздел и другие), пока в
этом ходе не вызвал скилл её типа и не прочитал её шаблон. Скилл несёт
правила типа, шаблон — форму сущности, и прочитанное в начале сессии к концу
длинного хода уже забыто — поэтому счёт идёт по ходам, а не по сессии.

Тип записи — место ∪ содержимое, и по каждому задетому типу нужны скилл и
шаблон. Место — ближайший владелец с типом: страница или блок, которому
место принадлежит детьми или отступом; уровни без тега пропускаются.
Содержимое — теги типа в тексте записи и тип самой цели правки, переноса
или удаления: новая строка `#задача` на +1 под этапом задевает задачу и
этап, переименование задачи — только задачу, её закрытие — задачу и этап.
Владельцев и путь к цели замок берёт из того, что агент уже прочитал в сессии
(hooks/tree.py), а чего там нет — из connect-API Craft (hooks/place.py,
hooks/owner.py, hooks/api.py). Цель не лежит ни на одной прочитанной странице — запись в
непрочитанное, и она под запретом. API недоступен — место не судится,
проверка по тегам текста идёт как обычно, а о том, что место не проверено,
замок раз за сессию говорит текстом в ход. Это единственный поход замка в
сеть, и только ради записи в Craft.

Что замок узнал о типе, он кладёт строкой `entity` в объём ревью хода
(hooks/review_row.py): ревью выбирает по ней критерии. Строка ложится на
`post-tool` записи — только за запись, которая случилась.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jarvis  # noqa: E402
import tree as read_tree  # noqa: E402
from entities import TagEntry, entity_reason, load_entities, load_tag_skills  # noqa: E402
from jarvis import events, wrappers  # noqa: E402
from payload import write_of  # noqa: E402
from place import BASE_ENV, Place, Verdict, page_types  # noqa: E402
from review_row import entity_row, hold_row, release_row, row_key  # noqa: E402
from state import (  # noqa: E402
    READS_KEY,
    SKILLS_KEY,
    STATE_FILE,
    TURN_KEY,
    TURN_READS_KEY,
    WARNED_KEY,
    BrokenFile,
    new_state,
    read_state,
    remember_reads,
    remember_turn_reads,
    state_lock,
)

MODULE_DIR = Path(__file__).resolve().parents[1]
# Разбор команд Craft: теги, id, цели записи, имена инструментов.
CRAFT_PARSER = 'command-tree-craft'
# Предупреждение о непроверенном месте записи: раз за сессию, текстом в ход.
# stderr хука до модели не доходит (замер 01.11.2026 живой сессией).
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
# Исходы, при которых место не проверено: причина идёт в след с этой пометкой.
UNCHECKED = (Verdict.UNAVAILABLE, Verdict.UNPARSED)
# Исходы, при которых место судилось и владельцы требуются.
JUDGED = (Verdict.LOCATED, Verdict.GUESSED, Verdict.NO_TARGET)
# Место не определено, и ход в этом не виноват (API не ответил, отдал не то
# или 404, срок вышел): запись проходит с предупреждением раз за сессию.
# Решение открыто у владельца; закрыть такую запись — поставить False.
UNAVAILABLE_PASSES = True
# Шаблон прочитан, когда он прочитан целиком: `blocks get ‹шаблон› --depth -1`.
TEMPLATE_READ_VERB = 'blocks get'
DEPTH_FLAG = 'depth'
WHOLE_DEPTH = '-1'


def skill_slug(tool_input) -> str:
    name = tool_input.get(SKILL_INPUT_FIELD) if isinstance(tool_input, dict) else None
    if not isinstance(name, str):
        return ''
    return name.rsplit(PLUGIN_SEPARATOR, 1)[-1].strip()


def library(runtime: jarvis.Runtime, slug: str):
    """Часть lib соседа из requires. Нет её — ошибка с советом переустановить."""
    found = runtime.library(slug)
    if found is None:
        raise RuntimeError(f'у модуля {slug} нет части lib. Переустанови набор из свежего источника')
    return found.load()


def deny_reason(entries: dict[str, TagEntry], tags: list[str], place: Place,
                missing: list[str], unread: list[str]) -> str:
    reasons = []
    if tags:
        pairs = ', '.join(f'{tag} → {entries[tag].skill}' for tag in tags)
        reasons.append(f'В записи есть тег типа: {pairs}.')
    for entity in place.entities:
        pairs = ', '.join(f'{tag} → {entries[tag].skill}' for tag in entity.tags)
        reasons.append(f'Запись задевает сущность «{entity.title}»: {pairs}.')
    for entity in place.edited:
        pairs = ', '.join(f'{tag} → {entries[tag].skill}' for tag in entity.tags)
        reasons.append(f'Запись правит сущность «{entity.title}»: {pairs}.')
    if missing:
        reasons.append(f'В этом ходе не вызван скилл: {", ".join(missing)}. '
                       f'Вызови его инструментом {SKILL_TOOL} и прочитай правила типа.')
    for tag in unread:
        reads = ' или '.join(f'«blocks get {template} --depth -1»' for template in entries[tag].templates)
        reasons.append(f'В этом ходе не прочитан шаблон {tag}: прочитай {reads}.')
    return ' '.join(reasons) + ' Потом повтори запись.'


def unread_reason(note: str) -> str:
    return (f'Запись в непрочитанное: {note}. Прочитай страницу, куда пишешь, '
            '«blocks get ‹id страницы› --depth -1», и повтори запись: по заголовку '
            'этой страницы замок узнаёт тип сущности.')


def untargeted_reason(note: str) -> str:
    return (f'Цель записи не найдена: {note}. Замок не узнает, куда ляжет запись. Укажи цель '
            'флагом с id блока (--id, --ids, --siblingId, --targetId, --targetSiblingId, --task, '
            '--document) и повтори запись.')


def broken_reason(error: ValueError) -> str:
    """Причина запрета, когда замок не смог судить запись: что сломано и как починить.

    Испорчен файл замка — причина называет именно его: удалить другой файл
    значило бы оставить запись закрытой.
    """
    reason = f'Замок craft-type-lock не смог судить запись: {error}. Запись в Craft закрыта до починки.'
    if isinstance(error, BrokenFile):
        reason += f' Чтобы починить, удали {error.path}: замок начнёт этот файл заново.'
    return f'{reason} Потом повтори запись.'


def template_reads(command: str, craft) -> list[str]:
    """Id, прочитанные целиком: аргументы `blocks get … --depth -1` каждой команды пачки."""
    found = []
    for part in craft.split_commands(command):
        parsed = craft.parse_command(part)
        if craft.verb_of(part) == TEMPLATE_READ_VERB and parsed.value(DEPTH_FLAG) == WHOLE_DEPTH:
            found.extend(item for item in parsed.arguments if craft.is_block_id(item))
    return craft.unique_ids(found)


def place_tail(place: Place) -> str:
    """Хвост причины для следа: что известно о месте записи."""
    if place.verdict in UNCHECKED:
        return f', место записи не проверено: {place.reason}'
    if place.verdict in JUDGED:
        return f', {place.reason}' if place.reason else ''
    raise ValueError(f'неизвестный исход проверки места: {place.verdict!r}')


def skills_of(entries: dict[str, TagEntry], entity) -> set[str]:
    return {entries[tag].skill for tag in entity.tags}


def settle(entries: dict[str, TagEntry], place: Place) -> Place:
    """Владельцы сменивших состояние сущностей — в место, кроме владельца того же модуля.

    Скилл закрытой сущности уже несёт правила такого владельца: этап закрывается
    внутри вехи скиллом этапов. Правило общее, по модулю, а не по имени типа.
    """
    extra = [owner for closed, owner in place.closing
             if skills_of(entries, owner) - skills_of(entries, closed)]
    owners = {entity.block_id.lower(): entity for entity in (*place.entities, *extra)}
    return place._replace(entities=tuple(owners.values()), closing=())


def needed_tags(tags: list[str], place: Place) -> list[str]:
    """Все задетые типы: место записи ∪ содержимое (теги текста и правленые цели)."""
    touched = [tag for entity in (*place.entities, *place.edited) for tag in entity.tags]
    return sorted({*tags, *touched})


def unread_templates(entries: dict[str, TagEntry], needed: list[str], turn_reads: list[str]) -> list[str]:
    """Теги, ни один шаблон которых не прочитан в этом ходе. Регистр id не важен."""
    read = {item.lower() for item in turn_reads}
    return [tag for tag in needed
            if not any(template.lower() in read for template in entries[tag].templates)]


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
            # Дерево прочитанного стирается: после сжатия агент читает страницу
            # заново, и замок не опирается на чтение, которого в контексте нет.
            read_tree.clear(storage)
        return jarvis.Silence(reason='после сжатия счёт ходов и скиллов сброшен, дерево стёрто')

    def on_post_tool(self, event, storage, craft) -> jarvis.Response:
        if craft.is_craft_read(event.tool_name):
            return self.on_read(event, storage, craft)
        if craft.is_craft_write(event.tool_name):
            return self.on_written(event, storage, craft)
        if event.tool_name != SKILL_TOOL:
            return jarvis.Silence(reason='вызван не скилл')
        slug = skill_slug(event.tool_input)
        if slug not in set(load_tag_skills(MODULE_DIR).values()):
            return jarvis.Silence(reason=f'скилл {slug or "без имени"} не скилл типа')
        with state_lock(storage):
            state = read_state(storage, craft)
            turn = state[TURN_KEY]
            storage.write_json(STATE_FILE, {**state, SKILLS_KEY: {**state[SKILLS_KEY], slug: turn}})
        return jarvis.Silence(reason=f'скилл {slug} вызван в ходе {turn}')

    def on_read(self, event, storage, craft) -> jarvis.Response:
        command = craft.command_of(event.tool_input)
        pages = craft.answer_page_ids(str(event.tool_result or ''))[:MAX_RESULT_PAGES]
        requested = craft.block_ids(command)
        ids = craft.unique_ids([*pages, *requested])
        if not ids:
            return jarvis.Silence(reason='чтение без id блока')
        with state_lock(storage):
            state = read_state(storage, craft)
            storage.write_json(STATE_FILE, {
                **state, READS_KEY: remember_reads(state[READS_KEY], ids, craft),
                # Шаблон прочитан, когда он сам прочитан целиком: страницы из
                # ответа, id из поиска и чтение не на всю глубину не считаются.
                TURN_READS_KEY: remember_turn_reads(state[TURN_READS_KEY], template_reads(command, craft),
                                                    craft)})
            tree = read_tree.load(storage, time.time())
            learned = read_tree.learn_read(tree, command, event.tool_result, craft)
            read_tree.save(storage, tree)
        return jarvis.Silence(reason=f'прочитано: {", ".join(item[:8] for item in ids)}; '
                                     f'в дерево страниц: {learned}')

    def on_written(self, event, storage, craft) -> jarvis.Response:
        """Запись случилась: её строка — в объём ревью, задетые страницы — читать заново.

        Команда без id цели (`--date`, `tasks add`) легла на страницу, которую
        замок по команде не узнает: дерево сбрасывается целиком, иначе
        следующая запись судилась бы по списку детей без только что записанного.
        """
        command = craft.command_of(event.tool_input)
        parts = craft.split_commands(command) or [command]
        blind = any(craft.write_targets(part).dated or not craft.target_ids(part) for part in parts)
        targets = craft.target_ids(command)
        with state_lock(storage):
            released = release_row(storage, row_key(event, command))
            tree = read_tree.load(storage, time.time())
            if blind:
                tree.apply('forget_all')
            elif targets:
                tree.apply('forget', targets)
            read_tree.save(storage, tree)
        row = 'строка типа в объёме' if released else 'отложенной строки нет'
        dropped = 'всё дерево: у записи нет id цели' if blind else str(len(targets))
        return jarvis.Silence(reason=f'запись прошла: {row}, сброшено в дереве: {dropped}')

    def warn_once(self, storage, craft, state: dict, reason: str) -> str | None:
        """Текст в ход о том, что место записи не проверяется; раз за сессию.

        Молчаливый отказ от проверки на всю сессию прятал бы запись в
        сущность без скилла; одного предупреждения хватает, повторять на
        каждой записи незачем. Второй и дальше — None.
        """
        if state[WARNED_KEY]:
            return None
        with state_lock(storage):
            fresh = read_state(storage, craft)
            storage.write_json(STATE_FILE, {**fresh, WARNED_KEY: True})
        return (f'{WARNING_PREFIX} {reason}. Тип страницы цели не проверен: запись в сущность '
                'без её скилла поймает только ревью хода.')

    def on_pre_tool(self, event, storage, craft) -> jarvis.Response:
        if not craft.is_craft_write(event.tool_name):
            return jarvis.Silence(reason='вызов не пишет в Craft')
        command = craft.command_of(event.tool_input)
        if not command.strip():
            return jarvis.Silence(reason='в вызове нет команды')
        try:
            entries = load_entities(MODULE_DIR)
        except ValueError as error:
            # Без справочника тег не опознать ни в команде, ни в заголовке
            # страницы. Пропуск отдал бы запись без скилла типа, поэтому
            # запись закрыта до починки.
            return jarvis.Deny(reason=entity_reason(error))
        tags = craft.find_tags(command, entries)
        try:
            state = read_state(storage, craft)
            tree = read_tree.load(storage, time.time())
            parts = [(craft.write_targets(part), write_of(craft.verb_of(part), craft.parse_command(part)))
                     for part in craft.split_commands(command) or [command]]
            place = settle(entries, page_types(parts, state[READS_KEY], os.environ.get(BASE_ENV),
                                               lambda text: craft.find_tags(text, entries), tree=tree))
            targets = [anchor for found, _ in parts for anchor in found.anchors]
            # Тип записи — в объём ревью, когда запись случится: только выбор
            # критериев, без решения замка. Что узнал из API — в дерево прочитанного.
            with state_lock(storage):
                hold_row(storage, row_key(event, command), entity_row(event, command, targets, place, tags))
                read_tree.save(storage, tree)
        except ValueError as error:
            # Ошибка хука харнес считает неблокирующей: упавший замок пропустил
            # бы запись. Испорченный файл замка или отступ — запрет с выходом.
            return jarvis.Deny(reason=broken_reason(error))
        return self.decide(entries, tags, place, storage, craft, state)

    def decide(self, entries, tags, place: Place, storage, craft, state: dict) -> jarvis.Response:
        """Запрет по месту, а без него — суждение по скиллам и шаблонам."""
        if place.verdict is Verdict.UNREAD:
            return jarvis.Deny(reason=unread_reason(place.reason))
        if place.verdict is Verdict.UNTARGETED:
            return jarvis.Deny(reason=untargeted_reason(place.reason))
        warning = None
        if place.verdict is Verdict.UNAVAILABLE:
            if not UNAVAILABLE_PASSES:
                return jarvis.Deny(reason=f'Место записи не определено: {place.reason}. Повтори запись позже.')
            warning = self.warn_once(storage, craft, state, place.reason)
        return self.judge(entries, tags, place, state, warning)

    def judge(self, entries: dict[str, TagEntry], tags: list[str], place: Place, state: dict,
              warning: str | None) -> jarvis.Response:
        """Пропуск или запрет по скиллам и шаблонам этого хода."""
        tail = place_tail(place)
        needed = needed_tags(tags, place)
        if not needed:
            return self.pass_through(f'тега типа нет{tail}', warning)
        skills = sorted({entries[tag].skill for tag in needed})
        missing = [slug for slug in skills if state[SKILLS_KEY].get(slug) != state[TURN_KEY]]
        unread = unread_templates(entries, needed, state[TURN_READS_KEY])
        if missing or unread:
            # Предупреждение не теряется и на запрете: иначе первый отказ API
            # на запрещённой записи съел бы его на всю сессию.
            reason = deny_reason(entries, tags, place, missing, unread)
            return jarvis.Deny(reason=f'{reason} {warning}' if warning else reason)
        found = f'теги {", ".join(tags)}: ' if tags else ''
        return self.pass_through(f'{found}скиллы вызваны и шаблоны прочитаны в этом ходе{tail}', warning)

    @staticmethod
    def pass_through(reason: str, warning: str | None) -> jarvis.Response:
        """Пропустить запись: молча с причиной в следе, либо текстом в ход.

        У текста в ход поля причины нет, поэтому причина входит в сам текст.
        """
        return jarvis.Context(text=f'{warning} ({reason})') if warning else jarvis.Silence(reason=reason)


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
