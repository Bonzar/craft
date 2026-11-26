"""Bounded, read-only diagnostics for one already-authorized source event."""
_STATES = frozenset(('pending', 'dispatching', 'completed', 'blocked'))
_FAILURES = frozenset(('batch_member_removed', 'provider_outcome_unknown',
    'provider_outcome_or_review_unconfirmed', 'batch_ModelError', 'batch_ValueError',
    'batch_ModelProviderUnavailable', 'batch_RuntimeError', 'batch_ProtectedContextError',
    'batch_LeaseLost', 'batch_OperationalError', 'batch_TimeoutError'))
_STAGES = (
    ('Construct thoughts for multiple source messages', 'draft_generation'),
    ('Verify source thought batch', 'batch_coverage_verification'),
)
_CALL_STATES = frozenset(('running', 'succeeded', 'failed', 'interrupted'))


def _exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def exact_event(db, event_id):
    """Caller must authorize the exact raw event before entering this projection."""
    if not _exists(db, 'memory_capture_batch_members') or not _exists(db, 'memory_capture_batches'):
        return None
    batch = db.execute('SELECT b.batch_id,b.state,b.error_code FROM memory_capture_batch_members m '
                       'JOIN memory_capture_batches b USING(batch_id) WHERE m.event_id=?', (event_id,)).fetchone()
    from .source_model_batch import CAPTURE_COVERAGE_CODES
    if batch is None:
        return None
    batch_id, state, error = batch
    result = {'state': state if state in _STATES else 'unknown',
              'failure_code': error if error is None or error in _FAILURES or error in CAPTURE_COVERAGE_CODES else 'unknown',
              'trace_coverage': 'not_recorded', 'model_stages': {}}
    if not _exists(db, 'memory_thought_runs') or not _exists(db, 'memory_thought_model_calls'):
        return result
    stages = dict(_STAGES)
    rows = db.execute("""SELECT c.stage,c.status FROM memory_thought_runs r
        JOIN memory_thought_model_calls c ON c.run_id=r.id
        WHERE r.operation='source_model_batch'
          AND json_extract(CASE WHEN json_valid(r.inputs) THEN r.inputs ELSE '{}' END,'$.capture_batch_id')=?
          AND c.stage IN (?,?)
        ORDER BY c.started_at DESC,c.id DESC LIMIT 8""", (batch_id,*stages))
    for stage, status in rows:
        result['model_stages'].setdefault(stages[stage],status if status in _CALL_STATES else 'unknown')
    if result['model_stages']:
        result['trace_coverage'] = 'recorded_stages_only'
    return result
