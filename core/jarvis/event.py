"""Единое событие: то, что обёртка кладёт перед модулем.

Состав полей задан разделом «Обёртка и события»: единое имя, идентификатор
сессии, инструмент и его вход, результат вызова, текст реплики, ответ человека
на вопрос, рабочий каталог, харнес-источник и сырое событие харнеса как есть.
Ничего сверх этого списка тут нет: чего харнес дал больше — лежит в `raw`.

Поле входит в формат, когда его даёт хотя бы один харнес, и называется по-нашему;
у харнеса, который его не даёт, оно пустое. Поэтому у «старта подагента» нет
поручения, а у «ответа и мысли модели» — признака «мысль или ответ»: ни Claude,
ни Codex их в событии не передают (замер 18.09.2026).
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
    error: str | None = None
    prompt_text: str | None = None
    human_answer: Any = None
    permission_options: tuple[Any, ...] = ()
    agent_id: str | None = None
    agent_type: str | None = None
    message_text: str | None = None
