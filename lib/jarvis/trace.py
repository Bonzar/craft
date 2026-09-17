"""След: журнал сессии в хранилище.

Каждый модуль пишет свой ответ на каждом своём событии: что за событие, какой
модуль, что ответил, почему. Молчание тоже ответ. По следу видно после хода,
какой модуль остановил вызов, какой пропустил его и какой промолчал.

Режим с его источником и признак автономии идут той же строкой: без них по
следу не понять, почему модуль промолчал.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .storage import SESSION, Storage

TRACE_FILE = 'trace.jsonl'


@dataclass(frozen=True)
class TraceLine:
    """Строка следа. Поля snake_case, как и в едином событии."""

    at: str
    event: str
    session_id: str
    module: str
    module_class: str
    response: str
    reason: str | None
    mode_enabled: bool
    mode_source: str
    autonomous: bool
    delivered: bool
    not_delivered_reason: str | None


class Trace:
    """Писарь следа одной сессии."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def write(
        self,
        event: str,
        module: str,
        module_class: str,
        response: str,
        mode_enabled: bool,
        mode_source: str,
        autonomous: bool,
        reason: str | None = None,
        delivered: bool = True,
        not_delivered_reason: str | None = None,
    ) -> TraceLine:
        line = TraceLine(
            at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            event=event,
            session_id=self._storage.session_id,
            module=module,
            module_class=module_class,
            response=response,
            reason=reason,
            mode_enabled=mode_enabled,
            mode_source=mode_source,
            autonomous=autonomous,
            delivered=delivered,
            not_delivered_reason=not_delivered_reason,
        )
        self._storage.append_line(
            TRACE_FILE,
            json.dumps(asdict(line), ensure_ascii=False),
            zone=SESSION,
        )
        return line

    def read(self) -> list[dict]:
        return [json.loads(line) for line in self._storage.read_lines(TRACE_FILE, zone=SESSION)]
