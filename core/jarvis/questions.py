"""Связь доставленных async-вопросов с точным выбором в следующей реплике.

Каждый поставщик хранит свой список: чтение одним не забирает ответ у другого.
Неподошедшая или неоднозначная реплика остаётся обычной репликой человека.
"""

import fcntl
import json
from contextlib import contextmanager

# Native capability Codex, замер 04.11.2026. MCP receipt не подтверждает показ.
ASYNC_QUESTION_TOOL = 'request_user_input_async'


@contextmanager
def lock_questions(storage, filename):
    path = storage.path(filename + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def parse_questions(value):
    if not isinstance(value, list):
        raise ValueError('вопросы должны быть списком')
    questions = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('вопрос должен быть объектом')
        title, options = item.get('title'), item.get('options', [])
        if not isinstance(title, str) or not title.strip():
            raise ValueError('у вопроса нет текста')
        if not isinstance(options, list) or not all(isinstance(option, str) and option for option in options):
            raise ValueError('варианты вопроса должны быть строками')
        questions.append({'title': title, 'options': options})
    return questions


def read_questions(storage, filename):
    try:
        value = json.loads(storage.path(filename).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    return parse_questions(value)


def delivered_questions(event):
    if event.tool_name != ASYNC_QUESTION_TOOL:
        return None
    tool_input, tool_result = event.tool_input, event.tool_result
    if isinstance(tool_result, str):
        try:
            tool_result = json.loads(tool_result)
        except ValueError:
            return None
    if not isinstance(tool_result, dict) or tool_result.get('accepted') is not True:
        return None
    if not isinstance(tool_input, dict) or 'questions' not in tool_input:
        return None
    return parse_questions(tool_input['questions'])


def remember_questions(storage, filename, event):
    questions = delivered_questions(event)
    if questions is None:
        return False
    with lock_questions(storage, filename):
        pending = {item['title']: item for item in read_questions(storage, filename)}
        pending.update({item['title']: item for item in questions})
        storage.write_json(filename, list(pending.values()))
    return True


def take_answer(storage, filename, prompt_text):
    if not isinstance(prompt_text, str):
        return None
    answer = prompt_text.strip()
    with lock_questions(storage, filename):
        pending = read_questions(storage, filename)
        matches = [item for item in pending if answer in item['options']]
        if len(matches) != 1:
            return None
        question = matches[0]
        storage.write_json(filename, [item for item in pending if item['title'] != question['title']])
    return question['title'], answer
