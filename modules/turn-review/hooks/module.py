#!/usr/bin/env python3
"""Ревью хода: хук не отпускает ход без вердикта подагента.

Ход — работа агента от реплики Влада до остановки. Смысловые дефекты записи в
Craft видны только целиком, в конце хода, поэтому ревью стоит на остановке.
Объём ревью копят поставщики: модули review-craft-calls, review-prompts,
session-anchor пишут свои записи через lib этого модуля. Критерии привозят
модули файлом data/review-criterion.md, формат записей поставщики описывают в
data/review-records.md. Хук на остановке собирает из них поручение подагенту
и смотрит вердикт по объёму: либо отпускает ход, либо возвращает его с
замечаниями или поручением.

На событии model-message модуль прячет ответ за ход с записями, пока нет
чистого вердикта: вместо ответа человек видит строку о том, что идёт ревью.
Модель этот текст не видит: меняется только показ. Там же отладочный вывод
(временный): при data/debug.json с {"debug": true} под показанным текстом
дописывается итог ревью.

В Craft и в сеть модуль не ходит: только хранилище и вход события.
"""

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import events, registry, wrappers  # noqa: E402

MODULE_DIR = Path(__file__).resolve().parents[1]
FRAME_FILE = MODULE_DIR / 'data' / 'review-frame.md'
# Подагент считает отпечаток скриптом, хук — функцией из lib: функция одна.
FINGERPRINT_SCRIPT = MODULE_DIR / 'hooks' / 'fingerprint.py'
DEBUG_FILE = MODULE_DIR / 'data' / 'debug.json'
DEBUG_FIELD = 'debug'
# дока, MessageDisplay: `final` — последний кусок сообщения.
FINAL_FIELD = 'final'

QUESTION_TOOL = 'AskUserQuestion'

MAX_NUDGES = 3
RELEASE_ACTION = 'release'
RELEASE_PHRASE = 'закончить без ревью'
QUESTION_TEXT = ('Ревью хода не состоялось после трёх поручений. '
                 'Провести ревью или закончить без него?')

NO_WRITES_REASON = 'записей в Craft не было'
# Ход-разговор: объём с прежних ходов ждёт следующего хода с записями.
QUIET_TURN_REASON = 'в этом ходе записей не было, объём ждёт хода с записями'
CLEAN_REASON = 'ревью чистое, ход отпущен'
RELEASED_REASON = 'ревью не было по решению человека'
SUBAGENT_REASON = 'вызов внутри подагента: ревью не касается'
PROBLEM_SEPARATOR = '. '

REPORT_SEPARATOR = '\n\n---\n'
# Ответ за ход с записями человек читает только после чистого вердикта.
HIDDEN_TEXT = 'Ревью хода идёт, отчёт после него.'
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


def answered_release(event, state, review):
    """Ответ кнопкой на наш вопрос о ревью — новое состояние; иначе None."""
    question = review.review_question(event.tool_input) if event.tool_name == QUESTION_TOOL else None
    if question is None:
        return None
    labels = chosen_labels(event.human_answer, question)
    if review.OPTION_RELEASE in labels:
        return replace(state, released=True)
    if review.OPTION_REVIEW in labels:
        return replace(state, nudges=0, released=False)
    return None


def problem_prefix(problem: str | None) -> str:
    return f'{problem[0].upper()}{problem[1:]}. ' if problem else ''


def required_criteria(state, runtime, review, volume: list[dict]):
    """Критерии, по которым принимается вердикт, и почему чистый вердикт не принять.

    Критерии и criteria_problem у stop и превью общие: одна функция lib.
    Нет ли поставщика (providers_problem), проверяет только stop.
    """
    return review.review_criteria(registry.modules_dir(runtime.module_dir), volume,
                                  state.criteria)


def read_frame(review) -> str:
    """Каркас поручения. Не читается — BriefError с путём, а не падение хука."""
    try:
        return FRAME_FILE.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as error:
        raise review.BriefError(f'каркас поручения {FRAME_FILE} не читается ({error})') from error


def nudge_text(storage, state, runtime, review, volume: list[dict],
               problem: str | None) -> tuple[str, tuple[str, ...]]:
    """Блок с просьбой запустить подагента и список критериев собранного поручения.

    Поручение собирается здесь из каркаса, записей поставщиков и критериев,
    чьё правило applies совпало с объёмом: always — всегда, craft — при
    записях write, tags — когда запись несёт тег из правила. Оно кладётся в
    хранилище сессии. Не разобранные файлы stop называет в problem. Каркас не
    прочитать или поручение не записать — BriefError.
    """
    parts = review.assemble_brief(registry.modules_dir(runtime.module_dir), volume,
                                  read_frame(review))
    brief = review.write_brief(storage, parts.text)
    volume_file = storage.path(review.VOLUME_FILE).resolve()
    return (
        f'{problem_prefix(problem)}Ход не отпущен: ревью хода не сделано. Запусти подагента '
        f'инструментом Agent (subagent_type general-purpose) не в фоне, дождись его ответа '
        f'тем же ходом. Поручение подагенту лежит в файле {brief}, передай ему пути: '
        f'объём {volume_file}, вердикт {storage.path(review.VERDICT_FILE).resolve()}, '
        f'volume_id {state.volume_id}, команда отпечатка '
        f'«python3 {FINGERPRINT_SCRIPT} {volume_file}»; записей write в объёме сейчас '
        f'{review.count_writes(volume)}, отпечаток {review.fingerprint(volume)}, критерии '
        f'{", ".join(parts.criteria) or "нет"}. Ответ до чистого вердикта человеку не показан: '
        f'напиши его заново после вердикта.'
    ), parts.criteria


def brief_failure_text(problem: str | None, error: Exception) -> str:
    """Блок, когда поручение не собрать: подагенту нечего дать, хук не падает."""
    return (f'{problem_prefix(problem)}Ход не отпущен: ревью хода не сделано, {error}. '
            'Скажи об этом человеку и закончи ход снова.')


def remarks_text(remarks: list[dict], review, problem: str | None = None) -> str:
    lines = [f'- [{item[review.CRITERION_KEY]}] {item[review.WHERE_KEY]}: {item[review.TEXT_KEY]}'
             for item in remarks]
    return (f'{problem_prefix(problem)}Ход не отпущен: ревью хода нашло замечания.\n'
            + '\n'.join(lines) + '\nПочини записи и снова закончи ход: хук попросит новое ревью.')


def stuck_text(problem: str | None, review, last_remarks: tuple[dict, ...]) -> str:
    # Ждать реплики модель не может: каждая остановка вернётся сюда же. Выход
    # из петли — только вопрос человеку. Блок, а не форма «вопрос»: на
    # остановке вопрос уходит контекстом, и Claude отпустил бы ход без него.
    # Человек решает по замечаниям, а не вслепую: последние замечания идут в
    # текст этого блока с вопросом, и агент показывает их с предложением по
    # каждому. Подагенту они не идут.
    context = ''
    if last_remarks:
        lines = '\n'.join(f'- [{item.get(review.CRITERION_KEY)}] {item.get(review.WHERE_KEY)}: '
                           f'{item.get(review.TEXT_KEY)}' for item in last_remarks)
        context = (f' Замечания последнего вердикта:\n{lines}\nПеред вопросом покажи человеку '
                   'каждое незакрытое замечание и предложи, что с ним сделать.')
    return (f'{problem_prefix(problem)}Ход не отпущен: {QUESTION_TEXT} Спроси человека '
            f'инструментом {QUESTION_TOOL}: вопрос «{QUESTION_TEXT}», варианты '
            f'«{review.OPTION_REVIEW}» и «{review.OPTION_RELEASE}». Вопрос и подписи вариантов скопируй '
            'дословно: по ним хук узнаёт ответ. Без этого вопроса ход не закончится: каждая '
            'остановка вернётся сюда. Человек может ответить и репликой, в которой только '
            f'фраза «{RELEASE_PHRASE}».{context}')


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


def elapsed_text(state) -> str:
    # Счёт выше MAX_NUDGES — метка заданного вопроса, а не поручение.
    nudges = min(state.nudges, MAX_NUDGES)
    started = state.first_nudge_at
    if not nudges:
        return 'Поручений не было.'
    if started is None:
        return f'Поручений {nudges}, время первого не записано.'
    try:
        seconds = int((now_utc() - datetime.fromisoformat(started)).total_seconds())
    except (ValueError, TypeError) as error:
        return f'Поручений {nudges}, время первого не читается ({started!r}: {error}).'
    return f'Поручений {nudges}, с первого прошло {seconds} с.'


def summary_text(head: str, state, review, volume: list[dict],
                 verdict: dict | None) -> str:
    """Итог ревью для показа: 3 строки — вердикт, объём, поручения и время."""
    counts = (f'Записей write {review.count_writes(volume)}, '
              f'read {review.count_kind(volume, review.KIND_READ)}.')
    if verdict is not None:
        counts = f'Критерии: {", ".join(verdict[review.CHECKED_KEY])}. {counts}'
    return f'{REPORT_TITLE} {head}\n{counts}\n{elapsed_text(state)}'


def note_summary(storage, saved, before, review, volume: list[dict], head: str,
                 verdict: dict | None = None) -> None:
    """Положить итог ревью в состояние для показа; вывод выключен — ничего.

    `before` — состояние до решения: в нём счёт поручений, время первого и
    показанный превью вердикт. `saved` — состояние после решения, итог ложится
    поверх него. Вердикт уже показан превью — итог сразу помечен показанным.
    """
    if not debug_on():
        return
    shown = verdict is not None and before.shown_verdict == review.verdict_key(verdict)
    review.save_state(storage, replace(
        saved, summary=summary_text(head, before, review, volume, verdict), summary_shown=shown))


def first_nudge_at(state) -> str | None:
    """Время первого поручения объёма, UTC; пишется только при отладочном выводе."""
    if state.first_nudge_at is not None or not debug_on():
        return state.first_nudge_at
    return now_utc().isoformat()


def displayed(base: str, report: str) -> jarvis.Response:
    """Показанный текст ответа и под ним отчёт."""
    return jarvis.UpdatedDisplay(text=f'{base}{REPORT_SEPARATOR}{report}')


def display_gate(event) -> str | None:
    """Причина не показывать итог под ответом; показывать — None."""
    enabled, problem = read_debug()
    if not enabled:
        return with_problem(DEBUG_OFF_REASON, problem)
    if not event.raw.get(FINAL_FIELD):
        return NOT_FINAL_REASON
    return None


def answer_hidden(storage, state, runtime, review) -> bool:
    """Прятать ли ответ: ход писал в Craft, а чистого вердикта по объёму нет.

    Вердикт с замечаниями или по другому объёму ответ не открывает: ход
    закончится блоком, и ответ придёт заново после чистого вердикта.
    Разрешение человека закончить без ревью открывает ответ. Состояние или
    строка объёма не читаются — ответ спрятан, даже без записей: починку и
    её уведомление делает stop.
    """
    volume, damage = review.parse_volume_file(storage.path(review.VOLUME_FILE))
    if state is None or damage:
        return True
    if not review.count_writes(volume) or review.writes_before_turn(volume, state.turn):
        return False
    if state.released:
        return False
    criteria, problem = required_criteria(state, runtime, review, volume)
    verdict = review.peek_verdict(storage, state.volume_id, criteria, problem)
    return not (verdict is not None and verdict[review.CLEAN_KEY]
                and verdict.get(review.FINGERPRINT_KEY) == review.fingerprint(volume))


class Module(jarvis.Module):
    """Решение на остановке хода и показ ответа до вердикта.

    Объём копят поставщики через lib, модуль его читает, а на остановке под
    локом чинит (load_volume) или очищает (clear_volume) файл. На реплике
    модуль берёт номер хода, на post-tool разбирает только ответ на
    собственный вопрос ревью.
    """

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
        review = load_review(runtime)
        with review.state_lock(runtime.storage):
            return handler(event, runtime, review)

    def on_prompt(self, event, runtime, review) -> jarvis.Response:
        # Поставщики на той же реплике берут номер той же функцией: кто бы
        # из параллельных хуков ни пришёл первым, ход у реплики один.
        turn, problem = review.claim_prompt_turn(runtime.storage, runtime.manifest.slug,
                                                 event.prompt_text or '')
        return jarvis.Silence(reason=with_problem(f'начался ход {turn}', problem))

    def on_after_compact(self, event, runtime, review) -> jarvis.Response:
        # Объём и вердикт лежат в хранилище и сжатие переживают, а правила
        # ревью из контекста ушли: счёт поручений начинается заново.
        storage = runtime.storage
        state, problem = review.load_state(storage)
        review.save_state(storage, replace(state, nudges=0))
        return jarvis.Silence(
            reason=with_problem('после сжатия счёт поручений обнулён, объём сохранён', problem))

    def on_model_message(self, event, runtime, review) -> jarvis.Response:
        # Подмена только показа: сообщение модели и её контекст не меняются.
        storage = runtime.storage
        # Показ идёт до остановки: состояние читается без починки, чинит stop.
        state = review.peek_state(storage)
        hidden = answer_hidden(storage, state, runtime, review)
        final = bool(event.raw.get(FINAL_FIELD))
        # Спрятанный ответ: вместо ответа, который ревью ещё не проверило,
        # человек видит строку про ревью, а с отладочным выводом под ней ещё
        # итог ревью. Куски до последнего прячутся пустотой.
        base = (HIDDEN_TEXT if final else '') if hidden else (event.message_text or '')
        gate = display_gate(event)
        if gate is not None or state is None:
            return jarvis.UpdatedDisplay(text=base) if hidden else jarvis.Silence(
                reason=gate or 'состояние не читается, итог покажет следующий ход')
        if state.summary and not state.summary_shown:
            review.save_state(storage, replace(state, summary_shown=True))
            return displayed(base, state.summary)
        return self.preview(base, hidden, runtime, state, review)

    def preview(self, base: str, hidden: bool, runtime, state, review) -> jarvis.Response:
        # У Claude финальное сообщение идёт до stop, а вердикт подагент пишет
        # раньше: показываем его сейчас, под ответом этого же хода.
        storage = runtime.storage
        volume = review.peek_volume(storage)
        criteria, problem = required_criteria(state, runtime, review, volume)
        verdict = review.peek_verdict(storage, state.volume_id, criteria, problem)
        if verdict is None or state.shown_verdict == review.verdict_key(verdict):
            if hidden:
                return jarvis.UpdatedDisplay(text=base)
            return jarvis.Silence(reason=with_problem(NO_SUMMARY_REASON, problem))
        review.save_state(storage, replace(state, shown_verdict=review.verdict_key(verdict)))
        text = summary_text(verdict_head(verdict, review), state, review, volume, verdict)
        return displayed(base, text)

    def on_post_tool(self, event, runtime, review) -> jarvis.Response:
        # Объём копят поставщики. Здесь только ответ на вопрос самого ревью.
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        if event.tool_name != QUESTION_TOOL:
            return jarvis.Silence(reason='вызов не вопрос человеку')
        storage = runtime.storage
        state, _ = review.load_state(storage)
        answered = answered_release(event, state, review)
        if answered is not None:
            review.save_state(storage, answered)
            return jarvis.Silence(
                reason=f'ответ на вопрос о ревью: released={answered.released}')
        return jarvis.Silence(reason='не ответ кнопкой на вопрос о ревью')

    def on_stop(self, event, runtime, review) -> jarvis.Response:
        # stop_hook_active=true — штатная повторная остановка после блока:
        # от петли защищает счёт поручений, а не этот признак.
        storage = runtime.storage
        state, state_problem = review.load_state(storage)
        volume, damage = review.load_volume(storage)
        if damage:
            # Объём уже не тот, что видел прежний вердикт: новый id делает
            # тот вердикт чужим, и ревью идёт по тому, что осталось.
            state = replace(state, volume_id=review.new_volume_id())
            review.save_state(storage, state)
        # Без поставщика объём пуст не потому, что записей не было: такой
        # ход молча не отпускается.
        no_provider = review.providers_problem(registry.modules_dir(runtime.module_dir))
        if not review.count_writes(volume) and no_provider is None:
            if damage:
                return jarvis.Block(reason=f'Ход не отпущен: {damage}. Скажи об этом человеку '
                                           'и закончи ход снова.')
            return jarvis.Silence(reason=with_problem(NO_WRITES_REASON, state_problem))
        criteria, criteria_problem = required_criteria(state, runtime, review, volume)
        blocking = joined(no_provider, criteria_problem)
        verdict, verdict_problem = review.read_verdict(
            storage, state.volume_id, review.fingerprint(volume), criteria)
        settled = None if verdict is None else self.settle(storage, state, runtime, review,
                                                            verdict, volume, blocking)
        if settled is not None:
            return settled
        repair = joined(state_problem, damage)
        problem = joined(repair, blocking, verdict_problem)
        if runtime.take_confirmation(RELEASE_ACTION) or state.released:
            cleared = review.clear_volume(storage, state)
            note_summary(storage, cleared, state, review, volume, REPORT_RELEASED)
            return jarvis.Silence(reason=with_problem(RELEASED_REASON, problem))
        if review.writes_before_turn(volume, state.turn):
            # Человек разговаривает с агентом, а не ждёт записи: поручение и
            # вопрос о ревью посреди разговора мешают, а объём никуда не
            # денется и пойдёт на ревью в конце ближайшего хода с записями.
            # Порча объёма или состояния молча не отпускается: о ней человек
            # узнаёт сейчас, как и на ходе без записей.
            if repair:
                return jarvis.Block(reason=f'Ход не отпущен: {repair}. '
                                           'Скажи об этом человеку и закончи ход снова.')
            return jarvis.Silence(reason=with_problem(QUIET_TURN_REASON, problem))
        return self.press(storage, state, runtime, review, volume, problem)

    def settle(self, storage, state, runtime, review, verdict: dict, volume: list[dict],
               blocking: str | None) -> jarvis.Response | None:
        """Ответ по вердикту нашего объёма; None — вердикт не принят.

        Чистый вердикт при проблеме установки (`blocking`: нет поставщика,
        не разобранные файлы, запись без критерия) не принимается: проверено
        не всё. Вердикт с замечаниями возвращает ход с ними и с этой проблемой.
        """
        head = verdict_head(verdict, review)
        if verdict[review.CLEAN_KEY] and blocking:
            return None
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
        # Вердикт снят: следующий с тем же отпечатком — уже другое ревью.
        # Замечания остаются в состоянии: вопрос человеку покажет их.
        note_summary(storage, replace(state, shown_verdict=None,
                                      last_remarks=tuple(verdict[review.REMARKS_KEY])),
                     state, review, volume, head, verdict)
        return jarvis.Block(reason=remarks_text(verdict[review.REMARKS_KEY], review, blocking))

    def press(self, storage, state, runtime, review, volume: list[dict],
              problem: str | None) -> jarvis.Response:
        nudges = state.nudges
        if nudges < MAX_NUDGES:
            try:
                text, criteria = nudge_text(storage, state, runtime, review, volume, problem)
            except review.BriefError as error:
                # Счёт идёт и здесь: без него сломанный каркас держал бы ход
                # по кругу, а так ход дойдёт до вопроса человеку.
                text, criteria = brief_failure_text(problem, error), state.criteria
            review.save_state(storage, replace(state, nudges=nudges + 1,
                                               first_nudge_at=first_nudge_at(state),
                                               criteria=criteria))
            return jarvis.Block(reason=text)
        if nudges == MAX_NUDGES:
            runtime.ask_confirmation(action=RELEASE_ACTION, phrase=RELEASE_PHRASE)
            review.save_state(storage, replace(state, nudges=nudges + 1))
        return jarvis.Block(reason=stuck_text(problem, review, state.last_remarks))


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
