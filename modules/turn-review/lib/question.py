"""Вопрос самого ревью о застрявшем ревью: его ответ разбирает хук, в объём он не идёт.

Модель может пересказать вопрос, тогда он узнаётся по паре подписей вариантов.
Здесь же текст блока, которым хук велит задать вопрос, когда поручения
кончились, и разбор ответа кнопкой в новое состояние.
"""

from dataclasses import replace

from .state import ReviewState
from .texts import problem_prefix, remark_line

QUESTION_TOOL = 'AskUserQuestion'
# Поручений до вопроса человеку; счёт выше — метка заданного вопроса.
MAX_NUDGES = 3
RELEASE_PHRASE = 'закончить без ревью'
QUESTION_TEXT = ('Ревью хода не состоялось после трёх поручений. '
                 'Провести ревью или закончить без него?')
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


def answered_release(tool_name: str | None, tool_input, answer,
                     state: ReviewState) -> ReviewState | None:
    """Ответ кнопкой на наш вопрос о ревью — новое состояние; иначе None."""
    question = review_question(tool_input) if tool_name == QUESTION_TOOL else None
    if question is None:
        return None
    labels = chosen_labels(answer, question)
    if OPTION_RELEASE in labels:
        return replace(state, released=True)
    if OPTION_REVIEW in labels:
        return replace(state, nudges=0, released=False)
    return None


def stuck_text(problem: str | None, last_remarks: tuple[dict, ...]) -> str:
    # Ждать реплики модель не может: каждая остановка вернётся сюда же. Выход
    # из петли — только вопрос человеку. Блок, а не форма «вопрос»: на
    # остановке вопрос уходит контекстом, и Claude отпустил бы ход без него.
    # Человек решает по замечаниям, а не вслепую: последние замечания идут в
    # текст этого блока с вопросом, и агент показывает их с предложением по
    # каждому. Подагенту они не идут.
    context = ''
    if last_remarks:
        lines = '\n'.join(remark_line(item) for item in last_remarks)
        context = (f' Замечания последнего вердикта:\n{lines}\nПеред вопросом покажи человеку '
                   'каждое незакрытое замечание и предложи, что с ним сделать.')
    return (f'{problem_prefix(problem)}Ход не отпущен: {QUESTION_TEXT} Спроси человека '
            f'инструментом {QUESTION_TOOL}: вопрос «{QUESTION_TEXT}», варианты '
            f'«{OPTION_REVIEW}» и «{OPTION_RELEASE}». Вопрос и подписи вариантов скопируй '
            'дословно: по ним хук узнаёт ответ. Без этого вопроса ход не закончится: каждая '
            'остановка вернётся сюда. Человек может ответить и репликой, в которой только '
            f'фраза «{RELEASE_PHRASE}».{context}')
