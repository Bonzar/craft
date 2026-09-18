"""Единый ответ модуля.

Восемь форм из раздела «Обёртка и события». Обёртка переводит форму в то, что
умеет её харнес; чего харнес не умеет — она называет, а не подменяет соседней
формой.

`kind` — не поле, а признак класса: иначе он встал бы первым позиционным
аргументом у каждой формы и `Context('текст')` молча задал бы форму, а не текст.
"""

from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

SILENCE = 'silence'
CONTEXT = 'context'
ALLOW = 'allow'
ASK = 'ask'
DENY = 'deny'
UPDATED_INPUT = 'updated-input'
BLOCK = 'block'
QUESTION = 'question'

ALL_KINDS = (SILENCE, CONTEXT, ALLOW, ASK, DENY, UPDATED_INPUT, BLOCK, QUESTION)


@dataclass(frozen=True)
class Response:
    """База всех форм. По `kind` обёртка и переводит ответ в форму харнеса."""

    kind: ClassVar[str] = ''


@dataclass(frozen=True)
class Silence(Response):
    """Молчание. Тоже ответ: в след оно пишется наравне с остальными.

    Причина необязательна и наружу не уходит — молчание харнесу ничего не
    печатает. Она нужна следу: без неё по журналу не отличить «правило не
    сработало» от «модуль просто ничего не сказал».
    """

    reason: str = ''

    kind: ClassVar[str] = SILENCE


@dataclass(frozen=True)
class Context(Response):
    """Текст модели — то, что модуль хочет сказать в ход."""

    text: str

    kind: ClassVar[str] = CONTEXT


@dataclass(frozen=True)
class Allow(Response):
    """Разрешить вызов."""

    reason: str = ''

    kind: ClassVar[str] = ALLOW


@dataclass(frozen=True)
class Ask(Response):
    """Спросить с причиной и фразой подтверждения.

    Фраза сверяется 1:1 на событии «реплика» и разрешает одно действие.
    """

    reason: str
    phrase: str
    action: str = ''

    kind: ClassVar[str] = ASK


@dataclass(frozen=True)
class Deny(Response):
    """Запретить с причиной."""

    reason: str

    kind: ClassVar[str] = DENY


@dataclass(frozen=True)
class UpdatedInput(Response):
    """Изменённый вход инструмента: вход целиком, а не правка одного поля."""

    tool_input: Mapping[str, Any] = field(default_factory=dict)

    kind: ClassVar[str] = UPDATED_INPUT


@dataclass(frozen=True)
class Block(Response):
    """Блок с причиной — для события «остановка хода» и «после вызова»."""

    reason: str

    kind: ClassVar[str] = BLOCK


@dataclass(frozen=True)
class Question(Response):
    """Вопрос человеку с вариантами.

    Обёртка переводит его в поручение модели вызвать инструмент вопроса
    харнеса, а без такого инструмента — в поручение спросить текстом.
    """

    text: str
    options: tuple[str, ...] = ()

    kind: ClassVar[str] = QUESTION
