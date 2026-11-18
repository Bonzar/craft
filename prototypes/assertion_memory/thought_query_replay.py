"""Reuse consumed query steps after failure; keep only references to exact trace payloads."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json

from .model import SemanticUnavailable
from .thought_worker import _Replay


@contextmanager
def query_replay(engine, trace, selection):
    identity = ('query-v1', hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest())
    original = engine.gateway

    def cached(key):
        with engine.store.transaction() as db:
            row = db.execute('SELECT response FROM memory_thought_generations WHERE request_key=?', (key,)).fetchone()
        if row is None:
            return None
        reference = json.loads(row[0])
        body = trace.payload(reference['response_hash'])
        trace.record('model_step_reused', details={'request_key': key, **reference})
        return body

    def save(key, body):
        response_hash = hashlib.sha256(body.encode()).hexdigest()
        call = next(row for row in reversed(trace.calls())
                    if row['status'] == 'succeeded' and row['response_hash'] == response_hash)
        reference = {'source_call_id': call['id'], 'request_hash': call['request_hash'],
                     'response_hash': response_hash}
        with engine.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_thought_generations VALUES(?,?) '
                'ON CONFLICT(request_key) DO NOTHING', (key, json.dumps(reference, sort_keys=True)))

    def invoke(request):
        context = json.loads(request.cache_context)
        return original(request.task, replace(request, cache_context=context['original']), context['bindings'])

    def invalidate(keys):
        with engine.store.transaction(write=True) as db:
            db.execute('DELETE FROM memory_thought_generations WHERE request_key IN (SELECT value FROM json_each(?))',
                (json.dumps(sorted(keys)),))
    replay = _Replay(invoke, cached, save, lambda: None, identity, invalidate)

    def gateway(task, request, bindings):
        context = {'bindings': bindings, 'original': request.cache_context}
        return replay(replace(request, cache_context=json.dumps(context, sort_keys=True)))

    # Contract recovery must not confirm a rejected provisional model response.
    gateway.discard_provisional = replay.discard_provisional
    gateway.reject_construct = replay.reject_construct
    engine.gateway = gateway
    try:
        yield
    except SemanticUnavailable as failure:
        # A known rejected meaning must be rebuilt, not replayed forever.
        if not getattr(failure,'checkpoint_invalidated',False):
            with engine.store.transaction(write=True) as db:
                replay.clear(db, 'memory_thought_generations')
        raise
    else:
        with engine.store.transaction(write=True) as db:
            replay.clear(db, 'memory_thought_generations')
    finally:
        engine.gateway = original
