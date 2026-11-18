"""Operational worker lanes and selective source fences; no model policy."""
from .model import ModelError


def run_lane(worker):
    worker.queue._maintenance_tick()
    from .policy_work import tick
    tick(worker.queue,worker.engine)
    if worker.lane == 'source':
        claim = worker.queue._claim(with_model=worker.model is not None, source_first=True, lane='source')
        return execute_claim(worker,claim) if claim is not None else None
    worker.names.reconcile_page(worker.engine)
    available = set()
    if worker.queue._seconds_until_work(with_model=worker.model is not None, lane='maintenance') == 0:
        available.add('source')
    if worker.derived.seconds_until_work(with_model=worker.engine.model is not None and worker.engine.verify_overviews) == 0:
        available.add('derived')
    if worker.names.seconds_until_work(with_model=worker.engine.naming_model is not None) == 0:
        available.add('editorial')
    chosen = worker.queue._maintenance_choice(available,worker.source_batch_limit,
        with_model=worker.model is not None,independent=True)
    if chosen == 'editorial':
        from .view_name_work import run_turn
        return run_turn(worker,fairness=True)
    if chosen == 'derived':
        return worker._run_derived(fairness=True)
    if chosen == 'source':
        claim = worker.queue._claim(with_model=worker.model is not None,source_first=True,lane='maintenance')
        return execute_claim(worker,claim) if claim is not None else None
    return None


def execute_claim(worker, claim):
    worker._notify('claimed','source',claim.job.id,claim.token)
    try:
        return worker._execute_owned_claim(claim)
    finally:
        worker._notify('released','source',claim.job.id,claim.token)


def link_source_state(queue, connection, assertion_id):
    from .source_reprocessing import freeze
    from .source_omission import revision
    owners = connection.execute("SELECT DISTINCT r.event_id FROM memory_extraction_runs r "
        "WHERE EXISTS(SELECT 1 FROM json_each(coalesce(json_extract(r.result,'$.source_revision.assertion_ids'),"
        "json_extract(r.result,'$.assertion_ids'),'[]')) member WHERE member.value=?)",(assertion_id,))
    state = {}
    for row in owners:
        frozen = freeze(queue,row['event_id'],connection)
        if assertion_id not in frozen.prior_ids:
            raise ModelError('Selected link source revision changed; fresh attempt required.')
        state[row['event_id']] = (frozen.fingerprint,revision(connection,row['event_id']),sorted(frozen.prior_ids))
    return state


def check_link_sources(queue, connection, source_id, proposals, states):
    selected = {source_id}
    for proposal in proposals:
        if proposal['status'] == 'eligible':
            selected.update((proposal['from_id'],proposal['to_id']))
    if any(link_source_state(queue,connection,key) != states[key] for key in selected):
        raise ModelError('Selected link source revision changed; fresh attempt required.')


def derived_model(worker, claim, provider, fairness):
    from .queue import _identity
    from .model import format_prompt
    from .derived_work import DerivedYield
    generated = 0
    def cooperative(request):
        nonlocal generated
        context = () if request.cache_context is None else (request.cache_context,)
        key = _identity('refresh-generation',worker.engine.pipeline_key,format_prompt(request),*context,
            *([claim.frontier['rebuild_operation']] if claim.frontier.get('rebuild_operation') else []))
        cached = worker.queue._refresh_generation(key)
        if cached is not None:
            worker.derived.renew(claim)
            return cached
        if worker.lane != 'maintenance' and generated >= (1 if fairness else 0) and worker.queue._ready_sources(with_model=worker.model is not None):
            raise DerivedYield()
        worker.derived.renew(claim)
        response = provider(request)
        generated += 1
        if isinstance(response,str) and response.strip():
            worker.derived.save_response(claim,key,response)
        return response
    return cooperative
