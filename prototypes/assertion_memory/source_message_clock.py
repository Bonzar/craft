"""Optional producer-message clocks, separate from this base's knowledge clock.

Validation establishes a typed producer claim, not truth of a world event or the
speaker's calendar. Native adapters additionally bind the claim to exact bytes.
"""
from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .storage import _timestamp

CONTRACT = 'source-message-clock-v1'
FIELDS = ('source_message_at', 'source_message_clock_basis')
_INSTANT = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})')

CLOCK_RULES = (
    ' Clock contract source-message-clock-v1: recorded_at and base_available_at are this base\'s '
    'knowledge/ingestion availability, not when the speaker sent the message or the described event held. '
    'Legacy source_recorded_at and source_available_at also describe base knowledge, never origin. '
    'source_message_at is optional producer-message clock provenance, with source_message_clock_basis; '
    'it is not a world-event observation date. Producer claims are attributed, not authenticated merely '
    'by being supplied. For relative today/yesterday/day-of-month use only the cited source_message_at '
    'with justified speaker calendar timezone, or exact cited body calendar context (including an '
    'explicit local today date). A UTC Z/offset transport serialization does not identify speaker '
    'timezone/calendar; native completion timestamps do not establish utterance time. Never substitute '
    'ingestion, processing/current date, OS locale or a task/session clock. Absent source clock remains '
    'unknown even with timezone. If the necessary origin/year/speaker calendar is unknown, preserve '
    'attributed relative meaning with no absolute valid_at. Exact absolute source-body dates may still '
    'justify day precision without origin metadata. Cite every necessary body anchor and source; '
    'clock metadata is context, not quoted prose or independent world-event evidence. A metadata token '
    'may redundantly name only approved source_message_at with a same-source actual temporal body '
    'anchor. Literal timestamps in source prose remain ordinary source quotations.'
)


def instant(value):
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        raise ValueError('source message clock requires a full offset-aware ISO datetime')
    if value[-1] != 'Z' and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError('invalid source message clock offset')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError('missing offset')
    except ValueError as error:
        raise ValueError('invalid source message clock instant') from error
    return _timestamp(parsed)


def normalize(metadata, *, source=None):
    """Copy/validate clock fields. Absence means unknown, never datetime.now()."""
    result = dict(metadata or {})
    if result.get('timezone') is not None:
        try:
            ZoneInfo(result['timezone'])
        except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError('source timezone must be a valid IANA timezone') from error
    if not any(key in result for key in FIELDS):
        return result
    stamp = instant(result.get('source_message_at'))
    basis = result.get('source_message_clock_basis')
    if not isinstance(basis, dict):
        raise ValueError('source message clock requires a supported clock basis')
    common = {'kind', 'field', 'value'}
    kind = basis.get('kind')
    if kind == 'producer':
        required, allowed = common | {'source'}, common | {'source', 'message_id'}
    elif kind == 'native-record':
        required = allowed = common | {'record_kind', 'path', 'start', 'end', 'sha256'}
    else:
        raise ValueError('unsupported source message clock basis kind')
    if not required <= basis.keys() or basis.keys() - allowed:
        raise ValueError('malformed source message clock basis')
    strings = required - {'start', 'end'}
    if any(not isinstance(basis[key], str) or not 1 <= len(basis[key]) <= 4096 for key in strings):
        raise ValueError('malformed source message clock basis fields')
    if instant(basis['value']) != stamp:
        raise ValueError('source message clock contradicts its basis value')
    basis = dict(basis)
    if kind == 'producer':
        if source is not None and basis['source'] != source:
            raise ValueError('source message clock basis belongs to another source')
        if 'message_id' in basis and (not isinstance(basis['message_id'], str) or not 1 <= len(basis['message_id']) <= 2048):
            raise ValueError('invalid source message clock producer identity')
        # Generic producers can retry Z/+00:00 equivalently. Native byte proof
        # instead retains the exact original spelling and producer precision.
        basis['value'] = instant(basis['value'])
    elif (type(basis['start']) is not int or type(basis['end']) is not int
          or not 0 <= basis['start'] < basis['end']
          or not re.fullmatch('[0-9a-f]{64}', basis['sha256'])
          or basis['field'] != 'timestamp'
          or basis['record_kind'] not in ('response_item/message/user', 'response_item/message/assistant',
                                         'event_msg/item_completed/UserMessage')):
        raise ValueError('invalid native source message clock witness')
    result.update(source_message_at=stamp, source_message_clock_basis=basis)
    return result


def replay(existing, supplied, *, allow_omitted=False, source=None):
    """Missing legacy fields are compatible, but never enriched or rewritten."""
    old = normalize(existing, source=source)
    new = normalize(supplied, source=source)
    if 'source_message_at' not in old:
        return
    if 'source_message_at' not in new and allow_omitted:
        return
    if any(old.get(key) != new.get(key) for key in FIELDS):
        raise ValueError('conflicting source message clock or exact clock basis')


def projection(metadata, *, source=None):
    value = normalize(metadata, source=source)
    return {key: value.get(key) for key in FIELDS}
