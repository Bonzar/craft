"""Постоянный номер операции Яндекс Банка из ID ленты.

ID ленты несёт версии цепочки и меняется между чтениями, например когда покупка
из «В обработке» становится выполненной. Номер операции внутри него постоянен.
"""

import base64
import binascii
import json

SOURCE = 'yabank'
FEED_PREFIX = 'FEED_VIEW'
OPERATION_SEGMENT = 4
CHAIN_MARKER = 'V4'


def decode(value: str) -> str:
    return base64.b64decode(value + '=' * (-len(value) % 4), validate=True).decode()


def chain_operation(parts: list) -> str | None:
    """У балльных операций номера в ленте нет, берём номер платежа сервиса из цепочки."""
    if CHAIN_MARKER not in parts or parts.index(CHAIN_MARKER) + 1 >= len(parts):
        return None
    chain = json.loads(decode(parts[parts.index(CHAIN_MARKER) + 1]))
    if not isinstance(chain, list) or not chain or not isinstance(chain[0], dict):
        return None
    value = chain[0].get('operationId')
    return value if isinstance(value, str) and value else None


def operation_id(feed_id: str) -> str | None:
    """None, если формат ID ленты неизвестен: номер не угадывается."""
    parts = feed_id.split('::') if isinstance(feed_id, str) else []
    if len(parts) <= OPERATION_SEGMENT or parts[0] != FEED_PREFIX:
        return None
    try:
        segment = parts[OPERATION_SEGMENT]
        return decode(segment) if segment else chain_operation(parts)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None


def canonical_marker_id(source: str, identifier: str) -> str:
    """Старые метки по ID ленты читаются как метки по номеру операции."""
    if source != SOURCE:
        return identifier
    return operation_id(identifier) or identifier
