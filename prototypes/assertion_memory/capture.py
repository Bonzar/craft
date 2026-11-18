"""Input to a durable capture of one attributed assertion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping


_DECLARATIONS = (
    ("proposal", re.compile(r"\s*(?:предложил[аио]?|предлага(?:ю|ем)|предложено|(?:это\s+)?(?:пока\s+)?только предложение|we propose|i propose)\b(?!\s+ли\b)", re.I)),
    ("observation", re.compile(r"\s*(?:проверил[аио]?|наблюдал[аио]?|увидел[аио]?|(?:i|we) (?:checked|observed))\b(?!\s+ли\b)", re.I)),
    ("implementation", re.compile(r"\s*(?:реализовал[аио]?|реализовано|(?:i|we) implemented)\b(?!\s+ли\b)", re.I)),
    ("inference", re.compile(r"\s*(?:(?:(?:мой|наш)\s+)?вывод\s*[:—]|(?:из этого|отсюда)\s+следует\b|conclusion\s*:)", re.I)),
    ("decision", re.compile(
        r'\s*(?:для (?:виджета )?проекта (?:«[^»]+»|"[^"]+"|[\w-]+)\s+)?'
        r"(?:решил[аио]?|согласовал[аио]?|договорил(?:ись|ся|ась|ось)|принял[аио]?\s+решение|we agreed|we decided)\b(?!\s+ли\b)", re.I,
    )),
)


def declared_kind(text: str, metadata: Mapping[str, Any] | None = None) -> str | None:
    kind = (metadata or {}).get("kind")
    verified = (metadata or {}).get("interpretation", {}).get("status") == "model_verified"
    if isinstance(kind, str) and kind and (kind != "assertion" or verified):
        return kind
    if text.rstrip().endswith("?"):
        return None
    return next((name for name, pattern in _DECLARATIONS if pattern.match(text)), None)


@dataclass(frozen=True)
class CaptureInput:
    text: str
    actor: str
    source: str
    episode: str
    assertion_id: str | None = None
    scope: str = "default"
    valid_at: str | date | None = None
    recorded_at: str | date | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RawEvent:
    id: str
    session_id: str
    turn_id: str
    prompt: str
    actor: str
    source: str
    scope: str
    recorded_at: str
    metadata: dict[str, Any]
