#!/usr/bin/env python3
"""Поставщик данных ревью для чата: реплики Влада и его ответы кнопками.

Запись идёт как сырьё: текст, авторство, время, номер хода. Вопрос самого
ревью о застрявшем ревью в объём не идёт: его разбирает хук ревью.

Номер хода реплики модуль берёт той же функцией, что ревью: хуки реплики
идут параллельно, и прочитанный счётчик мог бы ещё не вырасти.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import events, wrappers  # noqa: E402

REVIEW = 'turn-review'
QUESTION_TOOL = 'AskUserQuestion'
QUESTIONS_FIELD = 'questions'
QUESTION_FIELD = 'question'
AUTHOR = 'Влад'
AUTHOR_KEY = 'author'
AT_KEY = 'at'
SUBAGENT_REASON = 'вызов внутри подагента: в объём ревью не идёт'


def library(runtime):
    found = runtime.library(REVIEW)
    if found is None:
        raise RuntimeError(f'у модуля {REVIEW} нет части lib. Переустанови набор из свежего источника')
    return found.load()


def questions_text(tool_input) -> str:
    items = tool_input.get(QUESTIONS_FIELD) if isinstance(tool_input, dict) else None
    texts = [item.get(QUESTION_FIELD) for item in items if isinstance(item, dict)] \
        if isinstance(items, list) else []
    return '\n'.join(text for text in texts if isinstance(text, str))


def as_is(value):
    return value if isinstance(value, (str, dict, list)) else None


def with_problem(reason: str, problem: str | None) -> str:
    return f'{reason}. {problem}' if problem else reason


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Module(jarvis.Module):
    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        review = library(runtime)
        storage = runtime.storage
        if event.event == events.PROMPT:
            return self.on_prompt(event, runtime, review)
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        if event.tool_name != QUESTION_TOOL:
            return jarvis.Silence(reason='вызов не вопрос человеку')
        if review.is_review_question(event.tool_input):
            return jarvis.Silence(reason='вопрос самого ревью: его разбирает хук ревью')
        review.append_turn_record(storage, {
            review.KIND_KEY: review.KIND_ANSWER, review.QUESTION_KEY: questions_text(event.tool_input),
            review.ANSWER_KEY: as_is(event.tool_result), AUTHOR_KEY: AUTHOR, AT_KEY: now()})
        return jarvis.Silence(reason='ответ кнопками в объёме ревью')

    def on_prompt(self, event, runtime, review) -> jarvis.Response:
        # Номер берётся и на пустой реплике: модуль, пропустивший реплику,
        # сбил бы общий счёт ходов (см. claim_prompt_turn).
        storage, text = runtime.storage, event.prompt_text or ''
        with review.state_lock(storage):
            turn, problem = review.claim_prompt_turn(storage, runtime.manifest.slug, text)
        if not text.strip():
            return jarvis.Silence(reason=with_problem('пустая реплика', problem))
        review.append_record(storage, {
            review.KIND_KEY: review.KIND_PROMPT, review.RECORD_TURN_KEY: turn,
            review.PROMPT_TEXT_KEY: text, AUTHOR_KEY: AUTHOR, AT_KEY: now()})
        return jarvis.Silence(reason=with_problem('реплика в объёме ревью', problem))


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
