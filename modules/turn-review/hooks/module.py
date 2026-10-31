#!/usr/bin/env python3
"""Ревью хода: хук не отпускает ход без вердикта подагента.

Ход — работа агента от реплики Влада до остановки. Смысловые дефекты записи в
Craft видны только целиком, в конце хода, поэтому ревью стоит на остановке.
На вызовах модуль копит объём ревью: записи, чтения, вызовы скиллов, якорь.
На остановке смотрит вердикт подагента по этому объёму и либо отпускает ход,
либо возвращает его с замечаниями или поручением.

Отладочный вывод (временный): при data/debug.json с {"debug": true} итог
ревью дописывается к показу ответа модели на событии model-message. Модель
этот текст не видит: меняется только показ.

В Craft и в сеть модуль не ходит: только хранилище и вход события.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import events, registry, wrappers  # noqa: E402

MODULE_DIR = Path(__file__).resolve().parents[1]
BRIEF_FILE = MODULE_DIR / 'data' / 'review-brief.md'
DEBUG_FILE = MODULE_DIR / 'data' / 'debug.json'
DEBUG_FIELD = 'debug'
# дока, MessageDisplay: `final` — последний кусок сообщения.
FINAL_FIELD = 'final'

TYPE_LOCK = 'craft-type-lock'

# Имя сервера Craft MCP зависит от подключения, поэтому инструмент узнаём по
# хвосту имени, как замок типов.
MCP_PREFIX = 'mcp__'
MCP_NAME_SEPARATOR = '__'
CRAFT_WRITE_TOOLS = ('craft_write', 'craft_write_and_show')
CRAFT_READ_TOOL = 'craft_read'
COMMAND_FIELD = 'command'
SKILL_TOOL = 'Skill'
QUESTION_TOOL = 'AskUserQuestion'
QUESTIONS_FIELD = 'questions'
QUESTION_FIELD = 'question'
OPTIONS_FIELD = 'options'
LABEL_FIELD = 'label'
HEX = '[0-9a-fA-F]'
BLOCK_ID = re.compile(rf'{HEX}{{8}}-{HEX}{{4}}-{HEX}{{4}}-{HEX}{{4}}-{HEX}{{12}}')

# Якорь сессии видно двумя путями: Влад выбрал его кнопкой в вопросе про
# якорь или назвал репликой. Фраза в реплике считается, только когда с неё
# начинается реплика или предложение: «не беру задачу» якорем не будет.
ANCHOR_QUESTION_MARK = 'якор'
ANCHOR_PROMPT = re.compile(r'(?:^|[.!?\n])\s*(?:беру задачу|якорь:)', re.IGNORECASE)

MAX_NUDGES = 3
RELEASE_ACTION = 'release'
RELEASE_PHRASE = 'закончить без ревью'
OPTION_REVIEW = 'Провести ревью'
OPTION_RELEASE = 'Закончить без ревью'
QUESTION_TEXT = ('Ревью хода не состоялось после трёх поручений. '
                 'Провести ревью или закончить без него?')
QUESTION_MARK = 'Ревью хода не состоялось'
# Модель может пересказать вопрос своими словами. Тогда наш вопрос узнаём по
# паре подписей вариантов: иначе кнопка «Закончить без ревью» молча не
# сработала бы, и выходом осталась бы только фраза репликой.
OUR_OPTIONS = frozenset({OPTION_REVIEW, OPTION_RELEASE})

NO_WRITES_REASON = 'записей в Craft не было'
CLEAN_REASON = 'ревью чистое, ход отпущен'
RELEASED_REASON = 'ревью не было по решению человека'
SUBAGENT_REASON = 'вызов внутри подагента: в объём ревью не идёт'
PROBLEM_SEPARATOR = '. '

REPORT_SEPARATOR = '\n\n---\n'
REPORT_TITLE = 'Ревью хода:'
REPORT_CLEAN = 'чисто.'
REPORT_RELEASED = 'без ревью, по решению человека.'
REPORT_REMARKS_SHOWN = 3
REPORT_REMARK_CHARS = 60
DEBUG_OFF_REASON = 'отладочный вывод выключен'
NOT_FINAL_REASON = 'не последний кусок сообщения'
NO_SUMMARY_REASON = 'неотчитанного итога ревью нет'


def load_review(runtime):
    """Своя lib-часть: хранилище ревью и имена полей."""
    found = registry.library_of(runtime.manifest)
    if found is None:
        raise RuntimeError(f'у модуля {runtime.manifest.slug} нет части lib: хранилищу ревью '
                           'взяться неоткуда. Переустанови набор из свежего источника')
    return found.load()


def type_lock(runtime):
    """Библиотека замка типов: справочник тегов и поиск тегов в команде."""
    library = runtime.library(TYPE_LOCK)
    if library is None:
        raise RuntimeError(f'у модуля {TYPE_LOCK} нет части lib: поиск тегов типа взять '
                           'неоткуда. Переустанови набор из свежего источника')
    return library.load()


def craft_tool(tool_name) -> str:
    """Хвост имени инструмента Craft MCP; не Craft — пустая строка."""
    if not isinstance(tool_name, str) or not tool_name.startswith(MCP_PREFIX):
        return ''
    return tool_name.rsplit(MCP_NAME_SEPARATOR, 1)[-1]


def command_of(tool_input) -> str:
    command = tool_input.get(COMMAND_FIELD) if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else ''


def question_items(tool_input) -> list[dict]:
    items = tool_input.get(QUESTIONS_FIELD) if isinstance(tool_input, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def questions_text(tool_input) -> str:
    texts = [item.get(QUESTION_FIELD) for item in question_items(tool_input)]
    return '\n'.join(text for text in texts if isinstance(text, str))


def option_labels(item: dict) -> set[str]:
    options = item.get(OPTIONS_FIELD)
    if not isinstance(options, list):
        return set()
    labels = [option.get(LABEL_FIELD) for option in options if isinstance(option, dict)]
    return {label.strip() for label in labels if isinstance(label, str)}


def our_question(tool_input) -> str | None:
    """Текст нашего вопроса о ревью в вызове AskUserQuestion; нет его — None."""
    for item in question_items(tool_input):
        text = item.get(QUESTION_FIELD)
        if isinstance(text, str) and (QUESTION_MARK in text or OUR_OPTIONS <= option_labels(item)):
            return text
    return None


def as_is(value):
    """Результат вызова как есть, если это строка или объект JSON."""
    return value if isinstance(value, (str, dict, list)) else None


def chosen_labels(answer, question: str) -> set[str]:
    """Что человек выбрал в нашем вопросе: подписи целиком, без пробелов по краям.

    Ответ — словарь «текст вопроса → ответ». Берём только значение под нашим
    вопросом: в другом вопросе того же вызова подпись могла совпасть случайно.
    Строка вместо словаря — форма не замерена, тогда сверяется вся строка.
    """
    if isinstance(answer, str):
        return {answer.strip()}
    if not isinstance(answer, dict):
        return set()
    value = answer.get(question)
    items = value if isinstance(value, list) else [value]
    return {item.strip() for item in items if isinstance(item, str)}


def volume_record(event, turn: int, runtime, review) -> dict | None:
    """Строка объёма для вызова. Вызов ревью не касается — None."""
    tool, base = craft_tool(event.tool_name), {review.RECORD_TURN_KEY: turn}
    if tool in CRAFT_WRITE_TOOLS:
        command, tags = command_of(event.tool_input), type_lock(runtime)
        return {review.KIND_KEY: review.KIND_WRITE, **base, review.TOOL_KEY: event.tool_name,
                review.COMMAND_KEY: command, review.RESULT_KEY: as_is(event.tool_result),
                review.TYPES_KEY: tags.find_tags(command, tags.load_tag_skills())}
    if tool == CRAFT_READ_TOOL:
        command = command_of(event.tool_input)
        ids = list(dict.fromkeys(BLOCK_ID.findall(command)))
        return {review.KIND_KEY: review.KIND_READ, **base, review.COMMAND_KEY: command,
                review.IDS_KEY: ids}
    if event.tool_name == SKILL_TOOL:
        return {review.KIND_KEY: review.KIND_SKILL, **base,
                review.SKILL_KEY: type_lock(runtime).skill_slug(event.tool_input)}
    if event.tool_name == QUESTION_TOOL \
            and ANCHOR_QUESTION_MARK in questions_text(event.tool_input).lower():
        return {review.KIND_KEY: review.KIND_ANCHOR, **base,
                review.ANSWER_KEY: as_is(event.tool_result)}
    return None


def answered_release(event, state: dict, review) -> dict | None:
    """Ответ кнопкой на наш вопрос о ревью — новое состояние; иначе None."""
    question = our_question(event.tool_input) if event.tool_name == QUESTION_TOOL else None
    if question is None:
        return None
    labels = chosen_labels(event.human_answer, question)
    if OPTION_RELEASE in labels:
        return {**state, review.RELEASED_KEY: True}
    if OPTION_REVIEW in labels:
        return {**state, review.NUDGES_KEY: 0, review.RELEASED_KEY: False}
    return None


def problem_prefix(problem: str | None) -> str:
    return f'{problem[0].upper()}{problem[1:]}. ' if problem else ''


def nudge_text(storage, state: dict, runtime, review, writes: int, problem: str | None) -> str:
    types_file = Path(type_lock(runtime).TYPES_FILE).resolve()
    return (
        f'{problem_prefix(problem)}Ход не отпущен: ревью хода не сделано. Запусти подагента '
        f'инструментом Agent (subagent_type general-purpose) с брифом из файла {BRIEF_FILE}, '
        f'передай ему пути: объём {storage.path(review.VOLUME_FILE).resolve()}, '
        f'вердикт {storage.path(review.VERDICT_FILE).resolve()}, '
        f'volume_id {state[review.VOLUME_ID_KEY]}, типы {types_file}; записей write в объёме '
        f'сейчас {writes}. Дождись, пока подагент запишет вердикт, и закончи ход снова.'
    )


def remarks_text(remarks: list[dict], review) -> str:
    lines = [f'- [{item[review.CRITERION_KEY]}] {item[review.WHERE_KEY]}: {item[review.TEXT_KEY]}'
             for item in remarks]
    return ('Ход не отпущен: ревью хода нашло замечания.\n' + '\n'.join(lines)
            + '\nПочини записи и снова закончи ход: хук попросит новое ревью.')


def stuck_text(problem: str | None) -> str:
    # Ждать реплики модель не может: каждая остановка вернётся сюда же. Выход
    # из петли — только вопрос человеку. Блок, а не форма «вопрос»: на
    # остановке вопрос уходит контекстом, и Claude отпустил бы ход без него.
    return (f'{problem_prefix(problem)}Ход не отпущен: {QUESTION_TEXT} Спроси человека '
            f'инструментом {QUESTION_TOOL}: вопрос «{QUESTION_TEXT}», варианты '
            f'«{OPTION_REVIEW}» и «{OPTION_RELEASE}». Вопрос и подписи вариантов скопируй '
            'дословно: по ним хук узнаёт ответ. Без этого вопроса ход не закончится: каждая '
            'остановка вернётся сюда. Человек может ответить и репликой, в которой только '
            f'фраза «{RELEASE_PHRASE}».')


def with_problem(reason: str, problem: str | None) -> str:
    return f'{reason}{PROBLEM_SEPARATOR}{problem}' if problem else reason


def joined(*problems: str | None) -> str | None:
    return PROBLEM_SEPARATOR.join(problem for problem in problems if problem) or None


def read_debug() -> tuple[bool, str | None]:
    """Включён ли отладочный вывод и что не так с его файлом.

    Читается на каждом событии: переключать можно посреди сессии.
    """
    try:
        data = json.loads(DEBUG_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return False, None
    except ValueError as error:
        return False, f'файл {DEBUG_FILE} не JSON ({error}): вывод выключен'
    return isinstance(data, dict) and data.get(DEBUG_FIELD) is True, None


def debug_on() -> bool:
    return read_debug()[0]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def shortened(text: str) -> str:
    if len(text) <= REPORT_REMARK_CHARS:
        return text
    return text[:REPORT_REMARK_CHARS].rstrip() + '…'


def remarks_head(remarks: list[dict], review) -> str:
    shown = [f'[{item[review.CRITERION_KEY]}] {shortened(item[review.TEXT_KEY])}'
             for item in remarks[:REPORT_REMARKS_SHOWN]]
    rest = len(remarks) - len(shown)
    tail = f'; и ещё {rest}' if rest else ''
    return f'замечания ({len(remarks)}): {"; ".join(shown)}{tail}.'


def verdict_head(verdict: dict, review) -> str:
    if verdict[review.CLEAN_KEY]:
        return REPORT_CLEAN
    return remarks_head(verdict[review.REMARKS_KEY], review)


def elapsed_text(state: dict, review) -> str:
    # Счёт выше MAX_NUDGES — метка заданного вопроса, а не поручение.
    nudges = min(state[review.NUDGES_KEY], MAX_NUDGES)
    started = state[review.FIRST_NUDGE_KEY]
    if not nudges:
        return 'Поручений не было.'
    if started is None:
        return f'Поручений {nudges}, время первого не записано.'
    try:
        seconds = int((now_utc() - datetime.fromisoformat(started)).total_seconds())
    except (ValueError, TypeError) as error:
        return f'Поручений {nudges}, время первого не читается ({started!r}: {error}).'
    return f'Поручений {nudges}, с первого прошло {seconds} с.'


def summary_text(head: str, state: dict, review, volume: list[dict],
                 verdict: dict | None) -> str:
    """Итог ревью для показа: 3 строки — вердикт, объём, поручения и время."""
    counts = (f'Записей write {review.count_writes(volume)}, '
              f'read {review.count_kind(volume, review.KIND_READ)}.')
    if verdict is not None:
        counts = f'Критерии: {", ".join(verdict[review.CHECKED_KEY])}. {counts}'
    return f'{REPORT_TITLE} {head}\n{counts}\n{elapsed_text(state, review)}'


def note_summary(storage, saved: dict, before: dict, review, volume: list[dict], head: str,
                 verdict: dict | None = None) -> None:
    """Положить итог ревью в состояние для показа; вывод выключен — ничего.

    `before` — состояние до решения: в нём счёт поручений, время первого и
    показанный превью вердикт. `saved` — состояние после решения, итог ложится
    поверх него. Вердикт уже показан превью — итог сразу помечен показанным.
    """
    if not debug_on():
        return
    shown = verdict is not None \
        and before[review.SHOWN_VERDICT_KEY] == review.verdict_key(verdict)
    review.save_state(storage, {**saved, review.SUMMARY_KEY: summary_text(
        head, before, review, volume, verdict), review.SUMMARY_SHOWN_KEY: shown})


def first_nudge_at(state: dict, review) -> str | None:
    """Время первого поручения объёма, UTC; пишется только при отладочном выводе."""
    if state[review.FIRST_NUDGE_KEY] is not None or not debug_on():
        return state[review.FIRST_NUDGE_KEY]
    return now_utc().isoformat()


def displayed(event, report: str) -> jarvis.Response:
    """Показанный кусок ответа и под ним отчёт."""
    return jarvis.UpdatedDisplay(text=f'{event.message_text or ""}{REPORT_SEPARATOR}{report}')


def display_gate(event) -> str | None:
    """Причина молчать на ответе модели до лока и хранилища; показывать — None."""
    enabled, problem = read_debug()
    if not enabled:
        return with_problem(DEBUG_OFF_REASON, problem)
    if not event.raw.get(FINAL_FIELD):
        return NOT_FINAL_REASON
    return None


class Module(jarvis.Module):
    """Объём копится на вызовах, решение — на остановке хода."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        handlers = {
            events.PROMPT: self.on_prompt,
            events.POST_TOOL: self.on_post_tool,
            events.STOP: self.on_stop,
            events.AFTER_COMPACT: self.on_after_compact,
            events.MODEL_MESSAGE: self.on_model_message,
        }
        handler = handlers.get(event.event)
        if handler is None:
            return jarvis.Silence(reason=f'событие {event.event} ревью не нужно')
        # Куски ответа идут часто: без отладочного вывода хранилище не трогаем.
        gate = display_gate(event) if event.event == events.MODEL_MESSAGE else None
        if gate is not None:
            return jarvis.Silence(reason=gate)
        review = load_review(runtime)
        with review.state_lock(runtime.storage):
            return handler(event, runtime, review)

    def on_prompt(self, event, runtime, review) -> jarvis.Response:
        storage = runtime.storage
        state, problem = review.load_state(storage)
        turn = state[review.TURN_KEY] + 1
        review.save_state(storage, {**state, review.TURN_KEY: turn})
        text = event.prompt_text or ''
        if ANCHOR_PROMPT.search(text):
            review.append_record(storage, {review.KIND_KEY: review.KIND_ANCHOR,
                                           review.RECORD_TURN_KEY: turn, review.ANSWER_KEY: text})
            return jarvis.Silence(
                reason=with_problem(f'начался ход {turn}, якорь назван репликой', problem))
        return jarvis.Silence(reason=with_problem(f'начался ход {turn}', problem))

    def on_after_compact(self, event, runtime, review) -> jarvis.Response:
        # Объём и вердикт лежат в хранилище и сжатие переживают, а правила
        # ревью из контекста ушли: счёт поручений начинается заново.
        storage = runtime.storage
        state, problem = review.load_state(storage)
        review.save_state(storage, {**state, review.NUDGES_KEY: 0})
        return jarvis.Silence(
            reason=with_problem('после сжатия счёт поручений обнулён, объём сохранён', problem))

    def on_model_message(self, event, runtime, review) -> jarvis.Response:
        # Подмена только показа: сообщение модели и её контекст не меняются.
        storage = runtime.storage
        state, problem = review.load_state(storage)
        summary = state[review.SUMMARY_KEY]
        if summary and not state[review.SUMMARY_SHOWN_KEY]:
            review.save_state(storage, {**state, review.SUMMARY_SHOWN_KEY: True})
            return displayed(event, summary)
        return self.preview(event, storage, state, review, problem)

    def preview(self, event, storage, state: dict, review, problem) -> jarvis.Response:
        # У Claude финальное сообщение идёт до stop, а вердикт подагент пишет
        # раньше: показываем его сейчас, под ответом этого же хода.
        verdict = review.peek_verdict(storage, state[review.VOLUME_ID_KEY])
        if verdict is None or state[review.SHOWN_VERDICT_KEY] == review.verdict_key(verdict):
            return jarvis.Silence(reason=with_problem(NO_SUMMARY_REASON, problem))
        review.save_state(storage, {**state,
                                    review.SHOWN_VERDICT_KEY: review.verdict_key(verdict)})
        text = summary_text(verdict_head(verdict, review), state, review,
                            review.peek_volume(storage), verdict)
        return displayed(event, text)

    def on_post_tool(self, event, runtime, review) -> jarvis.Response:
        # Вызовы подагента — это рецензент, а не ход: его чтения в объёме
        # выдали бы «Связи» за прочитанные агентом.
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        storage = runtime.storage
        state, _ = review.load_state(storage)
        answered = answered_release(event, state, review)
        if answered is not None:
            review.save_state(storage, answered)
            return jarvis.Silence(
                reason=f'ответ на вопрос о ревью: released={answered[review.RELEASED_KEY]}')
        record = volume_record(event, state[review.TURN_KEY], runtime, review)
        if record is None:
            return jarvis.Silence(reason='вызов ревью не касается')
        review.append_record(storage, record)
        return jarvis.Silence(reason=f'в объём ревью: {record[review.KIND_KEY]}')

    def on_stop(self, event, runtime, review) -> jarvis.Response:
        # stop_hook_active=true — штатная повторная остановка после блока:
        # от петли защищает счёт поручений, а не этот признак.
        storage = runtime.storage
        state, state_problem = review.load_state(storage)
        volume, damage = review.load_volume(storage)
        if damage:
            # Объём уже не тот, что видел прежний вердикт: новый id делает
            # тот вердикт чужим, и ревью идёт по тому, что осталось.
            state = {**state, review.VOLUME_ID_KEY: review.new_volume_id()}
            review.save_state(storage, state)
        writes = review.count_writes(volume)
        if not writes:
            if damage:
                return jarvis.Block(reason=f'Ход не отпущен: {damage}. Скажи об этом человеку '
                                           'и закончи ход снова.')
            return jarvis.Silence(reason=with_problem(NO_WRITES_REASON, state_problem))
        volume_id = state[review.VOLUME_ID_KEY]
        verdict, verdict_problem = review.read_verdict(storage, volume_id, writes)
        if verdict is not None:
            return self.settle(storage, state, runtime, review, verdict, volume)
        problem = joined(state_problem, damage, verdict_problem)
        if runtime.take_confirmation(RELEASE_ACTION) or state[review.RELEASED_KEY]:
            cleared = review.clear_volume(storage, state)
            note_summary(storage, cleared, state, review, volume, REPORT_RELEASED)
            return jarvis.Silence(reason=with_problem(RELEASED_REASON, problem))
        return self.press(storage, state, runtime, review, volume, problem)

    def settle(self, storage, state: dict, runtime, review, verdict: dict,
               volume: list[dict]) -> jarvis.Response:
        head = verdict_head(verdict, review)
        if verdict[review.CLEAN_KEY]:
            cleared = review.clear_volume(storage, state)
            note_summary(storage, cleared, state, review, volume, head, verdict)
            # Разрешение фразой больше не нужно: ход отпущен вердиктом.
            runtime.take_confirmation(RELEASE_ACTION)
            return jarvis.Silence(reason=CLEAN_REASON)
        # Вердикт отработан: новый даст только новое ревью после починки.
        # Счёт поручений не трогаем: замечание, которое не закрыть, иначе
        # гоняло бы ход по кругу без вопроса человеку.
        review.drop_verdict(storage)
        # Вердикт снят: следующий с тем же числом записей — уже другое ревью.
        note_summary(storage, {**state, review.SHOWN_VERDICT_KEY: None}, state, review, volume,
                     head, verdict)
        return jarvis.Block(reason=remarks_text(verdict[review.REMARKS_KEY], review))

    def press(self, storage, state: dict, runtime, review, volume: list[dict],
              problem: str | None) -> jarvis.Response:
        nudges, writes = state[review.NUDGES_KEY], review.count_writes(volume)
        if nudges < MAX_NUDGES:
            review.save_state(storage, {**state, review.NUDGES_KEY: nudges + 1,
                                        review.FIRST_NUDGE_KEY: first_nudge_at(state, review)})
            return jarvis.Block(reason=nudge_text(storage, state, runtime, review, writes, problem))
        if nudges == MAX_NUDGES:
            runtime.ask_confirmation(action=RELEASE_ACTION, phrase=RELEASE_PHRASE)
            review.save_state(storage, {**state, review.NUDGES_KEY: nudges + 1})
        return jarvis.Block(reason=stuck_text(problem))


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
