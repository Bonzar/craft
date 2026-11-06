#!/usr/bin/env python3
"""Ревью хода: хук не отпускает ход без вердикта подагента.

Ход — работа агента от реплики Влада до остановки. Смысловые дефекты записи в
Craft видны только целиком, в конце хода, поэтому ревью стоит на остановке.
Объём ревью копят поставщики — модули с for = ["turn-review"]: они дописывают
строки в файл объёма хранилища сами, по контракту из README («Контракт
поставщиков»), и lib этого модуля не импортируют. Критерии привозят они же
файлом data/review-criterion.md. Формат строк объёма описывает сам ревью:
data/review-records.md этого модуля.
Хук на остановке собирает из них поручение подагенту
и смотрит вердикт по объёму: либо отпускает ход, либо возвращает его с
замечаниями или поручением.

На событии model-message модуль прячет ответ за ход с записями, пока нет
чистого вердикта: вместо ответа человек видит строку о том, что идёт ревью.
Модель этот текст не видит: меняется только показ. Там же отладочный вывод
(временный): при data/debug.json с {"debug": true} под показанным текстом
дописывается итог ревью.

Здесь точка входа и решения по событиям. Рядом в hooks/ то, что знает только
хук: display — показ ответа и итога, debug_output — флаг отладки, nudge —
поручение с путями хранилища, review_access — своя lib, поставщики и
поиск тегов.
Тексты и разбор ответа на вопрос ревью — в lib.

В Craft и в сеть модуль не ходит: только хранилище и вход события.
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))
# Соседние файлы хука грузятся простым импортом, как place у craft-type-lock;
# пакет jarvis они берут из того же пути поиска.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jarvis  # noqa: E402
from display import (  # noqa: E402
    HIDDEN_TEXT, answer_hidden, display_gate, displayed, first_nudge_at, is_final, note_summary,
    now_utc,
)
from jarvis import events, wrappers
from jarvis.questions import delivered_questions  # noqa: E402
from nudge import nudge_text  # noqa: E402
from review_access import SourcesError, load_review, required_criteria, sources  # noqa: E402

RELEASE_ACTION = 'release'

NO_WRITES_REASON = 'записей в Craft не было'
# Ход-разговор: объём с прежних ходов ждёт следующего хода с записями.
QUIET_TURN_REASON = 'в этом ходе записей не было, объём ждёт хода с записями'
CLEAN_REASON = 'ревью чистое, ход отпущен'
RELEASED_REASON = 'ревью не было по решению человека'
SUBAGENT_REASON = 'вызов внутри подагента: ревью не касается'
NO_SUMMARY_REASON = 'неотчитанного итога ревью нет'


class Module(jarvis.Module):
    """Решение на остановке хода и показ ответа до вердикта.

    Объём копят поставщики по контракту, модуль его читает, а на остановке
    под локом журнала чинит (load_volume) или очищает (clear_volume) файл. На
    реплике модуль связывает id реплики с номером хода, на post-tool разбирает только ответ на
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
        # Номер хода знает только ревью: поставщики кладут в записи id
        # реплики, и здесь id связывается с номером.
        turn, problem = review.claim_prompt_turn(runtime.storage, event)
        state, _ = review.load_state(runtime.storage)
        answer = (event.prompt_text or '').strip()
        has_grant = answer == review.RELEASE_PHRASE and runtime.take_confirmation(RELEASE_ACTION)
        has_question = (state.nudges > review.MAX_NUDGES
                        or state.question_fingerprint is not None or state.released)
        if has_question and answer == review.OPTION_REVIEW:
            self.restart_review(runtime, review, state)
        elif answer in (review.OPTION_RELEASE, review.RELEASE_PHRASE) and (has_question or has_grant):
            return self.accept_release(runtime, review, state)
        return jarvis.Silence(reason=review.with_problem(f'начался ход {turn}', problem))

    def accept_release(self, runtime, review, state) -> jarvis.Response:
        volume, damage = review.parse_volume_file(runtime.storage.path(review.VOLUME_FILE))
        fingerprint = review.fingerprint(volume)
        expected_scope = state.question_fingerprint or state.release_fingerprint
        is_stale = expected_scope is None or expected_scope != fingerprint
        if damage or is_stale:
            self.restart_review(runtime, review, state)
            return jarvis.Context('Ответ на прежний вопрос не принят: объём ревью изменился. '
                                  'Новые записи сохранены; выполни их автоматическое ревью.')
        runtime.cancel_confirmation(RELEASE_ACTION)
        review.save_state(runtime.storage, replace(
            state, released=True, release_fingerprint=fingerprint, question_fingerprint=None,
            is_question_delivered=False))
        return jarvis.Silence(reason='ответ человека принят для текущего отпечатка объёма')

    def on_after_compact(self, event, runtime, review) -> jarvis.Response:
        # Объём и вердикт лежат в хранилище и сжатие переживают, а правила
        # ревью из контекста ушли: счёт поручений начинается заново.
        storage = runtime.storage
        state, problem = review.load_state(storage)
        review.save_state(storage, replace(state, nudges=0))
        return jarvis.Silence(reason=review.with_problem(
            'после сжатия счёт поручений обнулён, объём сохранён', problem))

    def on_model_message(self, event, runtime, review) -> jarvis.Response:
        # Подмена только показа: сообщение модели и её контекст не меняются.
        storage = runtime.storage
        # Показ идёт до остановки: состояние читается без починки, чинит stop.
        state = review.peek_state(storage)
        if is_final(event) and state is not None and review.question_shown(event.message_text):
            volume, damage = review.parse_volume_file(storage.path(review.VOLUME_FILE))
            if (not damage and state.question_fingerprint is not None
                    and state.question_fingerprint == review.fingerprint(volume)):
                # Показываем вопрос, но не непроверенный отчёт вокруг него.
                review.save_state(storage, replace(state, is_question_delivered=True))
                question = (review.QUESTION_TEXT + '\n' + review.OPTION_REVIEW
                            + '\n' + review.OPTION_RELEASE)
                if state.last_remarks:
                    remarks = '\n'.join(review.remark_line(item) for item in state.last_remarks)
                    question = remarks + '\n\n' + question
                return jarvis.UpdatedDisplay(text=question)
        try:
            hidden = answer_hidden(storage, state, runtime, review)
        except SourcesError:
            # Поставщиков не прочитать — чистому вердикту не на что опереться:
            # ответ спрятан, а не показан непроверенным упавшим хуком. Причину
            # назовёт остановка.
            hidden = True
        final = is_final(event)
        # Спрятанный ответ: вместо ответа, который ревью ещё не проверило,
        # человек видит строку про ревью, а с отладочным выводом под ней ещё
        # итог ревью. Куски до последнего прячутся пустотой.
        base = (HIDDEN_TEXT if final else '') if hidden else (event.message_text or '')
        gate = display_gate(event, review)
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
        try:
            criteria, problem = required_criteria(state, runtime, review, volume)
        except SourcesError as error:
            # Поставщиков не прочитать — вердикту не на что опереться: ответ
            # остаётся спрятанным, причину назовёт остановка.
            if hidden:
                return jarvis.UpdatedDisplay(text=base)
            return jarvis.Silence(reason=str(error))
        verdict = review.peek_verdict(storage, state.volume_id, criteria, problem)
        if verdict is None or state.shown_verdict == review.verdict_key(verdict):
            if hidden:
                return jarvis.UpdatedDisplay(text=base)
            return jarvis.Silence(reason=review.with_problem(NO_SUMMARY_REASON, problem))
        review.save_state(storage, replace(state, shown_verdict=review.verdict_key(verdict)))
        text = review.summary_text(review.verdict_head(verdict), state, volume, verdict, now_utc())
        return displayed(base, text)

    def on_post_tool(self, event, runtime, review) -> jarvis.Response:
        # Объём копят поставщики. Здесь только ответ на вопрос самого ревью.
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        storage = runtime.storage
        state, _ = review.load_state(storage)
        if state.question_fingerprint is not None and review.question_accepted(delivered_questions(event)):
            volume, damage = review.parse_volume_file(storage.path(review.VOLUME_FILE))
            if damage or state.question_fingerprint != review.fingerprint(volume):
                self.restart_review(runtime, review, state)
                return jarvis.Context('Доставлен вопрос по прежним записям. Проведи ревью новых записей.')
            review.save_state(storage, replace(state, is_question_delivered=True))
            return jarvis.Silence(reason='вопрос показан, ждём ответа человека')
        if event.human_answer is None:
            return jarvis.Silence(reason='ответа человека нет')
        answered = review.answered_release(event.tool_name, event.tool_input,
                                           event.human_answer, state)
        if answered is not None:
            if answered.released:
                return self.accept_release(runtime, review, answered)
            self.restart_review(runtime, review, answered)
            return jarvis.Silence(
                reason=f'ответ на вопрос о ревью: released={answered.released}')
        return jarvis.Silence(reason='не ответ кнопкой на вопрос о ревью')

    def on_stop(self, event, runtime, review) -> jarvis.Response:
        # Шапку соседа не прочитать — ход не отпускается молча и не роняет
        # хук: падение харнес принял бы за пропуск.
        try:
            return self.stop_reviewed(runtime, review, event)
        except SourcesError as error:
            return jarvis.Block(reason=f'Ход не отпущен: {error}. Закончи ход снова после '
                                       'починки.')

    def stop_reviewed(self, runtime, review, event) -> jarvis.Response:
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
        # ход молча не отпускается. Строки замка без единой записи write —
        # тот же случай: записи были, а положить их в объём было некому.
        no_provider = review.joined(review.providers_problem(sources(runtime, review)),
                                    review.lost_writes_problem(volume))
        if not review.count_writes(volume) and no_provider is None:
            if damage:
                return jarvis.Block(reason=f'Ход не отпущен: {damage}. Скажи об этом человеку '
                                           'и закончи ход снова.')
            return jarvis.Silence(reason=review.with_problem(NO_WRITES_REASON, state_problem))
        criteria, criteria_problem = required_criteria(state, runtime, review, volume)
        blocking = review.joined(no_provider, criteria_problem)
        verdict, verdict_problem = review.read_verdict(
            storage, state.volume_id, review.fingerprint(volume), criteria)
        settled = None if verdict is None else self.settle(storage, state, runtime, review,
                                                            verdict, volume, blocking)
        if settled is not None:
            return settled
        repair = review.joined(state_problem, damage)
        problem = review.joined(repair, blocking, verdict_problem)
        if runtime.take_confirmation(RELEASE_ACTION) or state.released:
            if state.release_fingerprint != review.fingerprint(volume):
                state = self.restart_review(runtime, review, state)
                problem = review.joined(problem, 'после разрешения появились новые записи')
                return self.press(storage, state, runtime, review, volume, problem)
            return self.finish_review(runtime, review, state, volume, review.REPORT_RELEASED,
                                      review.with_problem(RELEASED_REASON, problem))
        waiting = self.wait_or_defer(event, runtime, review, state, volume, problem, repair)
        return waiting if waiting is not None else self.press(storage, state, runtime, review, volume, problem)

    def wait_or_defer(self, event, runtime, review, state, volume, problem, repair):
        storage = runtime.storage
        if state.question_fingerprint is not None:
            fingerprint = review.fingerprint(volume)
            if state.question_fingerprint != fingerprint:
                state = self.restart_review(runtime, review, state)
                return self.press(storage, state, runtime, review, volume, problem)
            if not repair and (state.is_question_delivered or review.question_shown(event.message_text)):
                review.save_state(storage, replace(state, is_question_delivered=True))
                return jarvis.Silence(reason='вопрос показан, объём ждёт ответа человека')
        if not state.review_requested and review.writes_before_turn(volume, state.turn, state.prompt_turns):
            # Человек разговаривает с агентом, а не ждёт записи: поручение и
            # вопрос о ревью посреди разговора мешают, а объём никуда не
            # денется и пойдёт на ревью в конце ближайшего хода с записями.
            # Порча объёма или состояния молча не отпускается: о ней человек
            # узнаёт сейчас, как и на ходе без записей.
            if repair:
                return jarvis.Block(reason=f'Ход не отпущен: {repair}. '
                                           'Скажи об этом человеку и закончи ход снова.')
            return jarvis.Silence(reason=review.with_problem(QUIET_TURN_REASON, problem))
        return None

    def settle(self, storage, state, runtime, review, verdict: dict, volume: list,
               blocking: str | None) -> jarvis.Response | None:
        """Ответ по вердикту нашего объёма; None — вердикт не принят.

        Чистый вердикт при проблеме установки (`blocking`: нет поставщика,
        не разобранные файлы, запись без критерия) не принимается: проверено
        не всё. Вердикт с замечаниями возвращает ход с ними и с этой проблемой.
        """
        head = review.verdict_head(verdict)
        if verdict[review.CLEAN_KEY] and blocking:
            return None
        if verdict[review.CLEAN_KEY]:
            return self.finish_review(runtime, review, state, volume, head, CLEAN_REASON, verdict)
        # Вердикт отработан: новый даст только новое ревью после починки.
        # Счёт поручений не трогаем: замечание, которое не закрыть, иначе
        # гоняло бы ход по кругу без вопроса человеку.
        review.drop_verdict(storage)
        # Вердикт снят: следующий с тем же отпечатком — уже другое ревью.
        # Замечания остаются в состоянии при любом отладочном выводе: вопрос
        # человеку покажет их.
        remarked = replace(state, shown_verdict=None,
                           last_remarks=tuple(verdict[review.REMARKS_KEY]))
        review.save_state(storage, remarked)
        note_summary(storage, remarked, state, review, volume, head, verdict)
        return jarvis.Block(reason=review.remarks_text(verdict[review.REMARKS_KEY], blocking))

    def restart_review(self, runtime, review, state):
        runtime.cancel_confirmation(RELEASE_ACTION)
        restarted = replace(state, released=False, release_fingerprint=None,
                            question_fingerprint=None, is_question_delivered=False,
                            review_requested=True, nudges=0)
        review.save_state(runtime.storage, restarted)
        return restarted

    def finish_review(self, runtime, review, state, volume, head, reason, verdict=None):
        storage = runtime.storage
        cleared = review.clear_volume(storage, state, expected_fingerprint=review.fingerprint(volume))
        if cleared is None:
            self.restart_review(runtime, review, state)
            return jarvis.Block('Объём изменился во время завершения. Проведи ревью новых записей.')
        note_summary(storage, cleared, state, review, volume, head, verdict)
        runtime.cancel_confirmation(RELEASE_ACTION)
        return jarvis.Silence(reason=reason)

    def press(self, storage, state, runtime, review, volume: list,
              problem: str | None) -> jarvis.Response:
        nudges = state.nudges
        if nudges < review.MAX_NUDGES:
            try:
                text, criteria = nudge_text(storage, state, runtime, review, volume, problem)
            except review.BriefError as error:
                # Счёт идёт и здесь: без него сломанный каркас держал бы ход
                # по кругу, а так ход дойдёт до вопроса человеку.
                text, criteria = review.brief_failure_text(problem, error), state.criteria
            review.save_state(storage, replace(state, nudges=nudges + 1,
                                               first_nudge_at=first_nudge_at(state),
                                               criteria=criteria))
            return jarvis.Block(reason=text)
        if nudges == review.MAX_NUDGES or state.question_fingerprint is None:
            runtime.ask_confirmation(action=RELEASE_ACTION, phrase=review.RELEASE_PHRASE)
            review.save_state(storage, replace(state, nudges=review.MAX_NUDGES + 1,
                                               question_fingerprint=review.fingerprint(volume),
                                               is_question_delivered=False))
        return jarvis.Question(text=review.stuck_text(problem, state.last_remarks),
                               options=(review.OPTION_REVIEW, review.OPTION_RELEASE))


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
