"""Local query correlation metadata; never included in model-visible requests."""
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

_current_trace = ContextVar('memory_query_usage_trace', default=None)
_usage_observer = ContextVar('memory_attempt_usage_observer', default=None)


def current_query_trace():
    return _current_trace.get()


@contextmanager
def observe_attempt_usage(observer):
    token=_usage_observer.set(observer)
    try:yield
    finally:_usage_observer.reset(token)


def notify_attempt_usage(status,attempt,duration_ms):
    observer=_usage_observer.get()
    if observer is not None:
        observer({'status':status,'actual_model':attempt.actual_model,
                  'duration_ms':duration_ms,'tokens':dict(attempt.values)})


@contextmanager
def query_usage_trace():
    """Generate a fresh query trace and restore the caller's context on every exit."""
    trace_id = str(uuid4())
    token = _current_trace.set(trace_id)
    try:
        yield trace_id
    finally:
        _current_trace.reset(token)
