"""Attempt-local taint: authorized foreign context is inference-only data.

Redact whole diagnostic payloads, not matching substrings: model paraphrases are
just as sensitive as quotations. No filtering is applied to inference requests.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json

from .model import ModelError

POLICY = 'foreign-context-inference-only-v1'
_STATE = ContextVar('memory_diagnostic_privacy', default=None)

@dataclass
class DiagnosticScope:
    restricted: bool = False

class ProtectedContextError(ModelError):
    def __init__(self):
        super().__init__('Protected context attempt failed; diagnostic content redacted')


def current_scope():
    return _STATE.get()


def restricted():
    state=current_scope()
    return state is not None and state.restricted


@contextmanager
def diagnostic_scope():
    if current_scope() is not None:
        yield current_scope()
        return
    token=_STATE.set(DiagnosticScope())
    try:
        yield current_scope()
    finally:
        _STATE.reset(token)


def diagnostic_hash(original_hash):
    # Never alias a historical unredacted payload stored under the raw hash.
    return hashlib.sha256((POLICY+':'+original_hash).encode()).hexdigest()


def redact(payload):
    wire=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=lambda _: '<object>')
    result={'redacted':True,'redaction_policy':POLICY,'hash_namespace':POLICY,
            'payload_sha256':hashlib.sha256(wire.encode()).hexdigest(),'serialized_chars':len(wire)}
    if isinstance(payload,dict):
        for key in ('request_hash','response_hash'):
            value=payload.get(key)
            if isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value):
                result[key]=value if payload.get('hash_namespace')==POLICY else diagnostic_hash(value)
        if payload.get('task') in ('Verify thought','Verify source thought batch'):
            result['task']=payload['task']
        if payload.get('verdict') in ('accept','reject','drill'):
            result['verdict']=payload['verdict']
        for field in ('records','bindings'):
            if isinstance(payload.get(field),(list,tuple)):
                result[field+'_count']=len(payload[field])
        if type(payload.get('attempt')) is int:
            result['attempt']=payload['attempt']
    return result
