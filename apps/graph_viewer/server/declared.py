"""Pure source declared-kind rules; mirrored without any store constructor."""
import re
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
