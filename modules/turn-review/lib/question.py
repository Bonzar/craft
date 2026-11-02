"""Вопрос самого ревью о застрявшем ревью: его ответ разбирает хук, в объём он не идёт.

Модель может пересказать вопрос, тогда он узнаётся по паре подписей вариантов.
"""

OPTION_REVIEW = 'Провести ревью'
OPTION_RELEASE = 'Закончить без ревью'
QUESTION_MARK = 'Ревью хода не состоялось'
OUR_OPTIONS = frozenset({OPTION_REVIEW, OPTION_RELEASE})
QUESTIONS_FIELD = 'questions'
QUESTION_FIELD = 'question'
OPTIONS_FIELD = 'options'
LABEL_FIELD = 'label'


def question_items(tool_input) -> list[dict]:
    items = tool_input.get(QUESTIONS_FIELD) if isinstance(tool_input, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def option_labels(item: dict) -> set[str]:
    options = item.get(OPTIONS_FIELD)
    if not isinstance(options, list):
        return set()
    labels = [option.get(LABEL_FIELD) for option in options if isinstance(option, dict)]
    return {label.strip() for label in labels if isinstance(label, str)}


def review_question(tool_input) -> str | None:
    """Текст вопроса о ревью в вызове AskUserQuestion; нет его — None."""
    for item in question_items(tool_input):
        text = item.get(QUESTION_FIELD)
        if isinstance(text, str) and (QUESTION_MARK in text or OUR_OPTIONS <= option_labels(item)):
            return text
    return None


def is_review_question(tool_input) -> bool:
    return review_question(tool_input) is not None
