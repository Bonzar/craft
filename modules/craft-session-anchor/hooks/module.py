#!/usr/bin/env python3
"""Якорь сессии: выбор кнопкой в вопросе про якорь или реплика «беру задачу».

Фраза в реплике считается, только когда с неё начинается реплика или
предложение: «не беру задачу» якорем не будет. Строку якоря модуль пишет в
объём ревью по контракту поставщиков из README turn-review: с id реплики
харнеса, одной строкой JSON через append_line хранилища ядра. lib ревью он
не импортирует, номер хода не считает: по id реплики его знает ревью.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import events, wrappers
from jarvis.questions import remember_questions, take_answer  # noqa: E402

# Контракт поставщиков ревью: файл объёма в зоне сессии и id реплики в строке.
VOLUME_FILE = 'turn-review-volume.jsonl'
PROMPT_ID_FIELD = 'prompt_id'
KIND_ANCHOR = 'anchor'
QUESTION_TOOL = 'AskUserQuestion'
PENDING_QUESTIONS = 'session-anchor-questions.json'
QUESTIONS_FIELD = 'questions'
QUESTION_FIELD = 'question'
ANCHOR_QUESTION_MARK = 'якор'
ANCHOR_PROMPT = re.compile(r'(?:^|[.!?\n])\s*(?:беру задачу|якорь:)', re.IGNORECASE)
SUBAGENT_REASON = 'вызов внутри подагента: в объём ревью не идёт'


def questions_text(tool_input) -> str:
    items = tool_input.get(QUESTIONS_FIELD) if isinstance(tool_input, dict) else None
    texts = [item.get(QUESTION_FIELD, item.get('title')) for item in items if isinstance(item, dict)] \
        if isinstance(items, list) else []
    return '\n'.join(text for text in texts if isinstance(text, str))


def as_is(value):
    return value if isinstance(value, (str, dict, list)) else None


def prompt_id_of(event) -> str | None:
    """Id реплики харнеса из сырого события; нет его или он не строка — None."""
    value = event.raw.get(PROMPT_ID_FIELD) or event.raw.get('turn_id')
    return value if isinstance(value, str) and value else None


def anchor_row(event, **fields) -> dict:
    """Строка якоря по контракту: вид, id реплики (если харнес его дал) и поля."""
    prompt_id = prompt_id_of(event)
    head = ({'kind': KIND_ANCHOR} if prompt_id is None
            else {'kind': KIND_ANCHOR, PROMPT_ID_FIELD: prompt_id})
    return {**head, **fields}


def append_row(runtime, row: dict) -> None:
    runtime.storage.append_line(VOLUME_FILE, json.dumps(row, ensure_ascii=False))


class Module(jarvis.Module):
    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if event.event == events.PROMPT:
            return self.on_prompt(event, runtime)
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        if remember_questions(runtime.storage, PENDING_QUESTIONS, event):
            return jarvis.Silence(reason='вопрос доставлен, ответа пока нет')
        question = questions_text(event.tool_input) if event.human_answer is not None else ''
        if ANCHOR_QUESTION_MARK not in question.lower():
            return jarvis.Silence(reason='вызов не вопрос о якоре')
        append_row(runtime, anchor_row(event, answer={'answers': as_is(event.human_answer)}, question=question))
        return jarvis.Silence(reason='якорь выбран кнопкой')

    def on_prompt(self, event, runtime) -> jarvis.Response:
        text = event.prompt_text or ''
        matched = take_answer(runtime.storage, PENDING_QUESTIONS, text)
        if matched is not None:
            question, answer = matched
            if ANCHOR_QUESTION_MARK in question.lower():
                append_row(runtime, anchor_row(event, answer=answer, question=question))
                return jarvis.Silence(reason='якорь выбран вариантом async-вопроса')
        if not ANCHOR_PROMPT.search(text):
            return jarvis.Silence(reason='реплика не называет якорь')
        append_row(runtime, anchor_row(event, answer=text))
        return jarvis.Silence(reason='якорь назван репликой')


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
