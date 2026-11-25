"""Low-cardinality, payload-free query timing events for a service journal."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import os
import re
from time import monotonic

from . import genai_traces

_CONTEXT = ContextVar('query_timing', default={})
_ID = re.compile(r'(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z')
STAGES = frozenset(('submission', 'ready_probe', 'queue_wait', 'job_total', 'result_persist',
    'query_total', 'release_lease', 'service_setup', 'snapshot', 'search', 'raw_search',
    'answer', 'answer_payload', 'diagnostics', 'usage_accounting', 'provider_attempt',
    'provider_setup', 'provider_cli', 'provider_network_wait', 'provider_response_read',
    'provider_decode', 'provider_retry_signal', 'trace_request_write', 'trace_response_write',
    'navigation', 'draft_generation', 'readiness_check', 'answer_verification', 'other_model',
    'gap_search', 'answer_neighborhood', 'model_contract_validation', 'query_outcome',
    'vm_http_submission'))
_STATUSES = frozenset(('start', 'ok', 'failed', 'timeout', 'interrupted', 'partial', 'ready',
    'queued', 'reused', 'miss', 'unavailable', 'connection_signal', 'retry_signal'))
_MODEL_STAGES = {'Navigate thought graph': 'navigation', 'Construct thought': 'draft_generation',
    'Check ready thought': 'readiness_check', 'Verify thought': 'answer_verification'}


def model_stage(name):
    return _MODEL_STAGES.get(name, 'other_model')


@contextmanager
def bind(*, trace_id=None, run_id=None, call_id=None, model_phase=None):
    previous = _CONTEXT.get()
    fields = dict(previous)
    for key, value in (('trace_id', trace_id), ('run_id', run_id), ('call_id', call_id)):
        if isinstance(value, str) and _ID.fullmatch(value):
            fields[key] = value
    if model_phase in (*_MODEL_STAGES.values(), 'other_model'):
        fields['model_phase'] = model_phase
    token = _CONTEXT.set(fields)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def active():
    return 'trace_id' in _CONTEXT.get()


def elapsed(started):
    return max(0, round((monotonic() - started) * 1000))


def _write_line(line):
    os.write(2, line)


def emit(stage, status, duration_ms=None, *, signal_count=None):
    context = _CONTEXT.get()
    if 'trace_id' not in context or stage not in STAGES or status not in _STATUSES:
        return
    if stage == 'query_outcome':
        genai_traces.outcome(status)
    elif stage == 'provider_retry_signal':
        genai_traces.provider_signal(status, signal_count)
    event = {'event': 'memory_query_timing', 'schema_version': 1, **context, 'stage': stage, 'status': status}
    if type(duration_ms) is int and 0 <= duration_ms < 2**53:
        event['duration_ms'] = duration_ms
    if type(signal_count) is int and 0 < signal_count < 2**31:
        event['signal_count'] = signal_count
    # One bounded newline-terminated write to the journal stream; never log arbitrary user
    # strings, exceptions, stdout/stderr, paths, caller IDs, or contents of the query.
    try:
        _write_line((json.dumps(event, separators=(',', ':')) + '\n').encode('ascii'))
    except OSError:
        pass  # Telemetry is best effort and must not turn a valid result into a failure.


def begin_otel(stage, *, queue_wait_ms=None):
    return genai_traces.begin(stage, _CONTEXT.get(), queue_wait_ms=queue_wait_ms)


@contextmanager
def span(stage, *, start=False):
    if not active():
        yield
        return
    begun = monotonic()
    otel = begin_otel(stage)
    status = 'ok'
    if start:
        emit(stage, 'start')
    try:
        yield
    except TimeoutError:
        status = 'timeout'
        raise
    except BaseException as error:
        # Compare a type's name locally, never put it in the journal.
        name = type(error).__name__
        status = ('timeout' if name == 'TimeoutExpired' else 'interrupted'
                  if name in ('CancelledError', 'ProcessingCancelled', 'KeyboardInterrupt')
                  else 'unavailable' if name == 'ModelProviderUnavailable'
                  else 'partial' if name == 'PartialMemoryAnswer'
                  else 'miss' if name == 'SemanticUnavailable' else 'failed')
        raise
    finally:
        genai_traces.end(otel, status)
        emit(stage, status, elapsed(begun))


def timed(stage):
    """Wrap a synchronous phase without timing unrelated background operations."""
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not active():
                return function(*args, **kwargs)
            with span(stage):
                return function(*args, **kwargs)
        return wrapped
    return decorator
