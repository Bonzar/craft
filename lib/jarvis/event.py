"""Единое событие: то, что обёртка кладёт перед модулем.

Состав полей задан разделом «Обёртка и события»: единое имя, идентификатор
сессии, инструмент и его вход, результат вызова, текст реплики, ответ человека
на вопрос, рабочий каталог, харнес-источник и сырое событие харнеса как есть.
Ничего сверх этого списка тут нет: чего харнес дал больше — лежит в `raw`.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Event:
    """Событие в едином формате. Неизменяемо: модуль его не правит, а отвечает."""

    event: str
    session_id: str
    cwd: str
    harness: str
    raw: Mapping[str, Any] = field(default_factory=dict)
    tool_name: str | None = None
    tool_input: Mapping[str, Any] | None = None
    tool_result: Any = None
    prompt_text: str | None = None
    human_answer: Any = None
