#!/usr/bin/env python3
"""Якорь сессии: выбор кнопкой в вопросе про якорь или реплика «беру задачу».

Фраза в реплике считается, только когда с неё начинается реплика или
предложение: «не беру задачу» якорем не будет. Номер хода реплики модуль
берёт той же функцией, что ревью: хуки реплики идут параллельно.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import events, wrappers  # noqa: E402

REVIEW = 'turn-review'
QUESTION_TOOL = 'AskUserQuestion'
QUESTIONS_FIELD = 'questions'
QUESTION_FIELD = 'question'
ANCHOR_QUESTION_MARK = 'якор'
ANCHOR_PROMPT = re.compile(r'(?:^|[.!?\n])\s*(?:беру задачу|якорь:)', re.IGNORECASE)
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


def with_problem(reason: str, problem: str | None) -> str:
    return f'{reason}. {problem}' if problem else reason


def as_is(value):
    return value if isinstance(value, (str, dict, list)) else None


class Module(jarvis.Module):
    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        review = library(runtime)
        storage = runtime.storage
        if event.event == events.PROMPT:
            return self.on_prompt(event, runtime, review)
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        question = questions_text(event.tool_input) if event.tool_name == QUESTION_TOOL else ''
        if ANCHOR_QUESTION_MARK not in question.lower():
            return jarvis.Silence(reason='вызов не вопрос о якоре')
        review.append_turn_record(storage, review.make_anchor_record(
            answer=as_is(event.tool_result), question=question))
        return jarvis.Silence(reason='якорь выбран кнопкой')

    def on_prompt(self, event, runtime, review) -> jarvis.Response:
        # Номер берётся на каждой реплике, даже не о якоре: модуль,
        # пропустивший реплику, сбил бы общий счёт ходов (см. claim_prompt_turn).
        storage, text = runtime.storage, event.prompt_text or ''
        with review.state_lock(storage):
            turn, problem = review.claim_prompt_turn(storage, runtime.manifest.slug, text)
        if not ANCHOR_PROMPT.search(text):
            return jarvis.Silence(reason=with_problem('реплика не называет якорь', problem))
        review.append_turn_record(storage, review.make_anchor_record(answer=text), turn=turn)
        return jarvis.Silence(reason=with_problem('якорь назван репликой', problem))


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
