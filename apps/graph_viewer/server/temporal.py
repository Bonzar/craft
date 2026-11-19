"""Knowledge membership is independent from valid-time effectiveness."""
from .common import timestamp


def available_at(row, metadata):
    interpretation = metadata.get('interpretation') or {}
    values = [row['recorded_at'], metadata.get('source_available_at'),interpretation.get('interpreted_at')]
    return max((timestamp(v) for v in values if v), default=None)


def eligible(row, metadata, selector, captured):
    cutoff = selector['at'] if selector['mode']=='known_then' else captured
    known = available_at(row, metadata)
    if known is None and selector['mode'] == 'known_then':
        return False
    if known is not None and known > cutoff:
        return False
    if selector['mode'] != 'known_now':
        return True
    valid = row['valid_at']
    if valid is None:
        return selector['undated'] in {'include','only'}
    return selector['undated']!='only' and timestamp(valid)<=selector['at']
