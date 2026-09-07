"""След решения: строка в ТОТ ЖЕ журнал решений и в том же формате, что пишет
.claude/hooks/lib/decision-log.js. Не новый файл и не новый формат — иначе
решения пакетов не попали бы в сводку вовсе.

Строка: {kind, ts, key, sid, call_id, event, hook, outcome, class}. Ключ даёт
обёртка (pylib/key.py), а имя события стоит рядом не для красоты: ключ не
уникален, и сшивают по паре «ключ и событие». Имя поля решателя — `hook`, как в
журнале: имя формата одно на всех, и переименовать его можно только вместе с
читателями.

Текста причины в журнале НЕТ — только КЛАСС: журнал переживает сессию, а причина
отказа содержит куски работы Влада. Класс — короткое имя решателя, ровно как в
lib/reason-class.js, и только у исключающих исходов.

Адаптером эта запись не является (решение 25): куда писать, говорит `state_dir`
из ядра события, его подставляет обёртка, и один и тот же код пишет туда, куда
ему дали. Адаптировать нечего.
"""

import datetime

from state import append_jsonl

FIRM = ("deny", "ask", "block")


def line(event, module, decision):
    """Строка следа. Отдельно от записи, чтобы её можно было проверить, не
    трогая диск."""
    event = event or {}
    outcome = (decision or {}).get("outcome") or ""
    return {
        "kind": "decision",
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "key": event.get("key") or "",
        "sid": event.get("session_id") or "",
        "call_id": event.get("call_id") or "",
        "event": event.get("event") or "",
        "hook": module,
        "outcome": outcome,
        "class": module if outcome in FIRM else "",
    }


def write(event, module, decision):
    """Дописать след. Возвращает, легла ли строка: потерянное решение читается
    как «решения не было», то есть как проход, и молчать об этом нельзя."""
    path = (event or {}).get("decision_log") or ""
    if not path:
        return False
    return append_jsonl(path, line(event, module, decision))
