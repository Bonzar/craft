"""Knowledge membership is independent from valid-time effectiveness."""
from functools import lru_cache
from .common import timestamp


def projection_timestamp_cache():
    """Per-snapshot memo for pure ISO normalization; no retained source state."""
    return lru_cache(maxsize=2048)(timestamp)


def available_at(row, metadata, *, normalize=None):
    if normalize is None:
        normalize=timestamp
    interpretation = metadata.get('interpretation') or {}
    values = [row['recorded_at'], metadata.get('source_available_at'),interpretation.get('interpreted_at')]
    return max((normalize(v) for v in values if v), default=None)


def eligible(row, metadata, selector, captured):
    return eligible_with_known(row, selector, captured, available_at(row, metadata))


def eligible_with_known(row, selector, captured, known, *, normalize=None):
    if normalize is None:
        normalize=timestamp
    cutoff = selector['at'] if selector['mode']=='known_then' else captured
    if known is None and selector['mode'] == 'known_then':
        return False
    if known is not None and known > cutoff:
        return False
    if selector['mode'] != 'known_now':
        return True
    valid = row['valid_at']
    if valid is None:
        return selector['undated'] in {'include','only'}
    return selector['undated']!='only' and normalize(valid)<=selector['at']
