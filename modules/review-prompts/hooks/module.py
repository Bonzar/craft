#!/usr/bin/env python3
"""Поставщик данных ревью для чата: реплики Влада и его ответы кнопками.

Запись идёт как сырьё: текст, авторство, время и id реплики харнеса. Номер
хода модуль не считает: по id реплики его знает ревью. Строки пишутся по
контракту поставщиков из README turn-review, одной строкой JSON через
append_line хранилища ядра; lib ревью модуль не импортирует.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import events, wrappers  # noqa: E402

# Контракт поставщиков ревью: файл объёма в зоне сессии и id реплики в строке.
VOLUME_FILE = 'turn-review-volume.jsonl'
PROMPT_ID_FIELD = 'prompt_id'
QUESTION_TOOL = 'AskUserQuestion'
QUESTIONS_FIELD = 'questions'
QUESTION_FIELD = 'question'
AUTHOR = 'Влад'
SUBAGENT_REASON = 'вызов внутри подагента: в объём ревью не идёт'


def questions_text(tool_input) -> str:
    items = tool_input.get(QUESTIONS_FIELD) if isinstance(tool_input, dict) else None
    texts = [item.get(QUESTION_FIELD) for item in items if isinstance(item, dict)] \
        if isinstance(items, list) else []
    return '\n'.join(text for text in texts if isinstance(text, str))


def as_is(value):
    return value if isinstance(value, (str, dict, list)) else None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def prompt_id_of(event) -> str | None:
    """Id реплики харнеса из сырого события; нет его или он не строка — None."""
    value = event.raw.get(PROMPT_ID_FIELD)
    return value if isinstance(value, str) and value else None


def volume_row(kind: str, event, **fields) -> dict:
    """Строка объёма по контракту: вид, id реплики (если харнес его дал) и поля вида."""
    prompt_id = prompt_id_of(event)
    head = {'kind': kind} if prompt_id is None else {'kind': kind, PROMPT_ID_FIELD: prompt_id}
    return {**head, **fields}


def append_row(runtime, row: dict) -> None:
    runtime.storage.append_line(VOLUME_FILE, json.dumps(row, ensure_ascii=False))


class Module(jarvis.Module):
    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if event.event == events.PROMPT:
            return self.on_prompt(event, runtime)
        if event.agent_id:
            return jarvis.Silence(reason=SUBAGENT_REASON)
        if event.tool_name != QUESTION_TOOL:
            return jarvis.Silence(reason='вызов не вопрос человеку')
        # Ответ на вопрос самого ревью тоже ложится: что это вопрос ревью,
        # знает только ревью, а поставщик пишет сырьё.
        append_row(runtime, volume_row(
            'answer', event, question=questions_text(event.tool_input),
            answer=as_is(event.tool_result), author=AUTHOR, at=now()))
        return jarvis.Silence(reason='ответ кнопками в объёме ревью')

    def on_prompt(self, event, runtime) -> jarvis.Response:
        text = event.prompt_text or ''
        if not text.strip():
            return jarvis.Silence(reason='пустая реплика')
        append_row(runtime, volume_row('prompt', event, text=text, author=AUTHOR, at=now()))
        return jarvis.Silence(reason='реплика в объёме ревью')


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
