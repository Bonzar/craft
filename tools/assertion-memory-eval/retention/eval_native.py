#!/usr/bin/env python3
"""Explicitly run a bounded anonymized native source-retention evaluation."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import time
from uuid import NAMESPACE_URL, uuid5

HERE = Path(__file__).resolve().parent
MANDATORY = tuple(f'R{i:03d}' for i in range(1, 11))
DIAGNOSTIC = 'A002-native-red'
MAX_CASES = 4


def load_cases():
    rows = json.loads((HERE / 'fixtures.json').read_text())
    selected = {row['id']: row for row in rows if row['id'] in MANDATORY}
    if set(selected) != set(MANDATORY):
        raise ValueError('Missing mandatory native fixture')
    # Synthetic reconstruction of inherited false negative, not private intake.
    selected[DIAGNOSTIC] = {
        'id': DIAGNOSTIC, 'shape': 'batch', 'label': 'retain',
        'messages': [
            {'id': 'u', 'actor': 'user', 'text': 'Мы выбрали сеанс 19:30. Учти его при планировании ужина.'},
            {'id': 'a', 'actor': 'assistant', 'text': 'Тогда считаю дорогу от кинотеатра после 21:00.'},
        ],
        'already_retained': [], 'max_thoughts': 1, 'required_primary_ref': 'u',
        'expected_meaning': 'Сеанс 19:30 выбран; учитывать при планировании ужина. '
                            'Не приписывать источнику вид сеанса или время выезда.',
        'known_red': 'Старый и новый реальные прогоны пропускали валидный выбор после отказа '
                     'проверяющего. В новой версии больше нет ложной частичной записи.',
    }
    return selected


def grade(case, result):
    wanted = case.get('native_expectations', {})
    allowed = {row['id'] for row in case['messages'] + case['already_retained']}
    main_refs = {row['id'] for row in case['messages']}
    minimum = 1 if case['label'] == 'retain' else 0
    maximum = case.get('max_thoughts')
    checks = {
        'raw_count': result['raw_count'] == wanted.get('raw_messages', len(case['messages'])),
        'raw_integrity': result['raw_integrity'],
        'construct_count': 1 <= result['construct_count'] <= wanted.get('max_construct_calls', 1),
        'worker_complete': result['worker_complete'] == wanted.get('worker_complete', True),
        'thought_count': len(result['thoughts']) >= minimum and (maximum is None or len(result['thoughts']) <= maximum),
        'refs': all(bool(thought['refs']) and set(thought['refs']) <= allowed and
                    bool(main_refs.intersection(thought['refs'])) for thought in result['thoughts']),
        'primary_ref': not case.get('required_primary_ref') or
                       any(case['required_primary_ref'] in thought['refs'] for thought in result['thoughts']),
    }
    return {
        'checks': checks,
        'mechanical_pass': all(checks.values()) and not case.get('known_red'),
        'diagnostic': 'known_red' if case.get('known_red') else None,
        'semantic_review_pending': bool(result['thoughts']) or case['label'] == 'review',
    }


def run_case(case, model, review_model):
    # Imports are delayed until CLI constraints and model permission flag have passed.
    from prototypes.assertion_memory.capture_batch import authorized_machine_batch, capture, run_next
    from prototypes.assertion_memory.mcp_server import ServiceConfig, run_worker
    from prototypes.assertion_memory.storage import MemoryStore

    session = str(uuid5(NAMESPACE_URL, 'anonymous-retention-eval:' + case['id']))
    batch = case['shape'] == 'batch'
    with TemporaryDirectory(prefix='retention-native-eval-') as scratch:
        db = Path(scratch) / 'fixture.sqlite3'
        config = ServiceConfig(db=str(db), provider='codex', model=model, review_model=review_model,
                               reasoning_effort='medium', review_reasoning_effort='low', codex_job_timeout=90,
                               processing_session_ids=[] if batch else [session], worker_idle_seconds=.01)
        with MemoryStore(db) as store:
            service = config.service(store)
            event_refs, prior_thought_refs = {}, {}
            for order, row in enumerate(case['already_retained'], 1):
                source = 'fixture://anonymous-retention/' + case['id'] + '/' + row['id']
                actor = 'fixture-owner' if row['actor'] == 'user' else row['actor']
                event = service.capture_message(session, row['id'], actor, row['actor'], row['text'],
                                                order, source=source)['event']['id']
                event_refs[event] = row['id']
                prior = service.thoughts.publish(row['text'], origin={
                    'kind': 'source_interpretation', 'event_id': event, 'session_id': session,
                    'role': row['actor'], 'actor': actor, 'source': source},
                    bases=[{'event_id': event}], metadata={'verification': 'accepted', 'fixture_seed': True})
                prior_thought_refs[prior.id] = row['id']
                with store.transaction(write=True) as conn:
                    conn.execute("UPDATE memory_capture_jobs SET status='completed',phase='done' WHERE event_id=?", (event,))
            stream = service.ingest_register('anonymous-retention-fixture', session)
            messages = [
                dict(session_id=session, message_id=row['id'], actor_id='fixture-owner' if row['actor'] == 'user' else row['actor'],
                     role=row['actor'], text=row['text'], position=len(case['already_retained']) + i,
                     source='fixture://anonymous-retention/' + case['id'] + '/' + row['id'],
                     delivery={k: stream[k] for k in ('stream_id', 'epoch')} | {'seq': i})
                for i, row in enumerate(case['messages'], 1)
            ]
            if batch:
                with authorized_machine_batch():
                    receipts = capture(service, hashlib.sha256(session.encode()).hexdigest(), messages)['receipts']
            else:
                receipts = [service.capture_message(**messages[0])]
            raw_ids = [row['event']['id'] for row in receipts]
            event_refs.update(zip(raw_ids, (row['id'] for row in case['messages']), strict=True))
            started = time.monotonic()
            if batch:
                run_next(config, store, lambda: False)
            else:
                run_worker(config, limit=1)
            elapsed = round(time.monotonic() - started, 3)
            raw = [service.queue.get_event(event) for event in raw_ids]
            jobs = [service.queue.get(event) for event in raw_ids]
            with store.transaction() as conn:
                calls = [dict(row) for row in conn.execute(
                    'SELECT stage,status,duration_ms FROM memory_thought_model_calls ORDER BY started_at')] if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='memory_thought_model_calls'").fetchone() else []
                states = [row[0] for row in conn.execute('SELECT state FROM memory_capture_batches')] if batch else []
            utterances = [
                {'text': row.text, 'refs': [event_refs.get(basis.get('event_id'),
                                            prior_thought_refs.get(basis.get('thought_id'), '<unknown>')) for basis in row.bases]}
                for row in service.thoughts.list_thoughts() if row.id not in prior_thought_refs
            ]
            return {'raw_count': sum(row is not None for row in raw),
                    'raw_integrity': all(row and row.prompt == expected['text']
                                         for row, expected in zip(raw, case['messages'], strict=True)),
                    'worker_complete': all(job and job.status == 'completed' and job.phase == 'done' for job in jobs)
                                       and (not batch or states == ['completed']),
                    'construct_count': sum(row['stage'].startswith('Construct') for row in calls),
                    'calls': calls, 'batch_states': states, 'thoughts': utterances, 'elapsed_seconds': elapsed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', choices=[*MANDATORY, DIAGNOSTIC])
    parser.add_argument('--runtime-root', type=Path, help='Exact checked-out or archived engine source root')
    parser.add_argument('--revision', help='Declared immutable Git revision of runtime-root')
    parser.add_argument('--execute', action='store_true', help='Explicitly use your already-authorized Codex account')
    parser.add_argument('--report', type=Path, help='New destination JSON; never overwritten')
    args = parser.parse_args(argv)
    cases = load_cases()
    if not args.execute:
        print('Validated 10 mandatory cases and one known-red diagnostic; no model calls made.')
        return 0
    if not args.case or len(set(args.case)) != len(args.case) or len(args.case) > MAX_CASES:
        parser.error('--execute requires 1–4 distinct explicit --case IDs')
    if (args.runtime_root is None or args.report is None or args.report.exists()
            or not re.fullmatch(r'[0-9a-f]{40}', args.revision or '')):
        parser.error('--execute requires exact --runtime-root, 40-hex --revision and a new --report path')
    runtime = args.runtime_root.resolve()
    source = runtime / 'prototypes/assertion_memory'
    if not all((source / name).is_file() for name in ('capture_batch.py', 'dialogue.py', 'source_model_batch.py')):
        parser.error('runtime source tree is missing batch/model modules')
    sys.path.insert(0, str(runtime))
    outcomes = []
    for name in args.case:
        case = cases[name]
        try:
            observed = run_case(case, 'gpt-6.2-sol', 'gpt-6.2-sol')
            verdict = grade(case, observed)
            outcomes.append(dict(id=name, expected=case['expected_meaning'], **observed, **verdict))
            print(name, 'known red' if verdict['diagnostic'] else
                      ('mechanical pass; semantic review pending' if verdict['semantic_review_pending'] else
                       'mechanical pass') if verdict['mechanical_pass'] else 'mechanical failure',
                  flush=True)
        except Exception as failure:
            outcomes.append({'id': name, 'provider_or_harness_error': type(failure).__name__,
                             'diagnostic': 'known_red' if case.get('known_red') else None})
            print(name, type(failure).__name__, flush=True)
    report = dict(declared_revision=args.revision,
                  source_hashes={p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in
                                 ('dialogue.py','source_model_batch.py','thought_processing.py')},
                  fixture_sha256=hashlib.sha256((HERE / 'fixtures.json').read_bytes()).hexdigest(),
                  model='gpt-6.2-sol', verifier='gpt-6.2-sol', model_effort='medium', verifier_effort='low',
                  totals=dict(Counter('diagnostic' if r.get('diagnostic') else 'mechanical_pass'
                                      if r.get('mechanical_pass') else 'failure' for r in outcomes)), cases=outcomes)
    with args.report.open('x') as result:
        json.dump(report, result, ensure_ascii=False, indent=2)
        result.write('\n')
    # Known-red is diagnostic, not hidden: it does not report a green model score.
    return 1 if any(not row.get('mechanical_pass') for row in outcomes) else 0


if __name__ == '__main__':
    raise SystemExit(main())
