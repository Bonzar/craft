"""Optional generation-attempt accounting; never stores prompts or responses.

The sidecar is operational telemetry, independent of the knowledge database.
Counts are provider-reported, not estimates. Cached input and reasoning output
are subsets of input and output respectively and must not be added to totals.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

from .usage_context import current_query_trace, notify_attempt_usage

_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
_TASKS = frozenset({
    'Construct thought', 'Verify thought', 'Verify source thought batch',
    'Check ready thought', 'Find useful shared thought question', 'Disambiguate thought support',
    'Navigate thought graph',
    'Generate memory answer',
    'Review source material coverage', 'Repair omitted source meanings',
    'Discover reusable memory themes', 'Review thematic source membership',
    'Plan reusable memory theme hierarchy', 'Verify contextual assertion interpretations',
    'Verify source interpretation withdrawals', 'Check source withdrawals against full-source coverage',
    'Propose evidence links for the source assertion', 'Verify semantic evidence links',
    'Review attributed thematic map', 'Plan memory retrieval for a question',
    'Write attributed thematic map', 'Name memory view', 'Review memory view name',
    'Repair rejected contextual assertion interpretations',
    'Extract attributable assertions from the user event',
    'Review grounded memory overview', 'Draft grounded memory contribution',
    'Review grounded memory contribution', 'Synthesize grounded memory overview',
})
_SCHEMA = '''CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, task TEXT NOT NULL, provider TEXT NOT NULL,
 requested_model TEXT, actual_model TEXT, selection_basis TEXT, trace_id TEXT, duration_ms INTEGER, request_chars INTEGER, output_chars INTEGER,
 input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER,
 reasoning_output_tokens INTEGER);
 CREATE INDEX IF NOT EXISTS attempts_started ON attempts(started_at);
 CREATE TABLE IF NOT EXISTS tracking (id INTEGER PRIMARY KEY CHECK(id=1), started_at TEXT NOT NULL);'''


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _warn():
    try:
        print('Model usage tracking unavailable; generation continues.', file=sys.stderr)
    except Exception:
        pass


def _label(value):
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/+@-]{1,160}', value):
        return value
    return 'hashed:' + hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _task(value):
    return value if isinstance(value, str) and value in _TASKS else 'unknown:' + hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _count(value):
    return value if type(value) is int and 0 <= value <= 9223372036854775807 else None


def normalize_usage(usage, protocol):
    """Validate known provider formats without estimating missing token counts."""
    if not isinstance(usage, dict):
        return dict.fromkeys(_FIELDS)
    if protocol == 'codex':
        values = {key: _count(usage.get(key)) for key in _FIELDS}
    elif protocol == 'openai-chat':
        prompt = usage.get('prompt_tokens_details')
        completion = usage.get('completion_tokens_details')
        values = dict(zip(_FIELDS, (
            _count(usage.get('prompt_tokens')),
            _count(prompt.get('cached_tokens')) if isinstance(prompt, dict) else None,
            _count(usage.get('completion_tokens')),
            _count(completion.get('reasoning_tokens')) if isinstance(completion, dict) else None,
        )))
    else:
        return dict.fromkeys(_FIELDS)
    for subset, total in (('cached_input_tokens', 'input_tokens'), ('reasoning_output_tokens', 'output_tokens')):
        if values[subset] is not None and (values[total] is None or values[subset] > values[total]):
            values[subset] = None
    return values


class _Attempt:
    def __init__(self):
        self.values = dict.fromkeys(_FIELDS)
        self.actual_model = None
        self.output_chars = None

    def observe(self, response, protocol):
        """Observe only an explicitly known provider envelope; discard its body."""
        try:
            if protocol == 'codex':
                self._codex(response)
            elif protocol == 'openai-chat' and isinstance(response, dict):
                self.values = normalize_usage(response.get('usage'), protocol)
                self.actual_model = _label(response.get('model'))
        except Exception:
            _warn()

    def _codex(self, response):
        if isinstance(response, bytes):
            response = response.decode('utf-8', errors='replace')
        if not isinstance(response, str):
            return
        turns = []
        for line in response.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(event, dict) and event.get('type') == 'turn.completed':
                if isinstance(event.get('model'), str) and event['model']:
                    self.actual_model = _label(event['model'])
                turns.append(normalize_usage(event.get('usage'), 'codex'))
        if turns:
            self.values = {key: _count(sum(turn[key] for turn in turns))
                           if all(turn[key] is not None for turn in turns) else None
                           for key in _FIELDS}


class UsageLedger:
    """Explicitly enabled, best-effort SQLite sidecar writer."""
    def __init__(self, path):
        self.path = Path(path)
        self.available = False
        try:
            with self._connect() as connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.executescript(_SCHEMA)
                connection.execute('BEGIN IMMEDIATE')
                columns = {row[1] for row in connection.execute('PRAGMA table_info(attempts)')}
                for name, kind in (('selection_basis','TEXT'), ('trace_id','TEXT'), ('duration_ms','INTEGER')):
                    if name not in columns:
                        connection.execute(f'ALTER TABLE attempts ADD COLUMN {name} {kind}')
                connection.execute('CREATE INDEX IF NOT EXISTS attempts_trace ON attempts(trace_id)')
                connection.execute('INSERT OR IGNORE INTO tracking VALUES (1,?)', (_now(),))
            self.available = True
        except Exception:
            _warn()

    @classmethod
    def for_database(cls, memory_db):
        return cls(str(memory_db) + '.model-usage.sqlite3')

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=0.1)
        try:
            connection.execute('PRAGMA busy_timeout=100')
            with connection:
                yield connection
        finally:
            connection.close()

    def _start(self, task, provider, model, request_chars, selection_basis):
        if not self.available:
            return None
        identifier = str(uuid.uuid4())
        try:
            with self._connect() as connection:
                connection.execute('INSERT INTO attempts '
                    '(id,started_at,status,task,provider,requested_model,request_chars,selection_basis,trace_id) VALUES (?,?,?,?,?,?,?,?,?)',
                    (identifier, _now(), 'in_progress', _task(task), _label(provider),
                     _label(model), _count(request_chars), _label(selection_basis), current_query_trace()))
            return identifier
        except Exception:
            _warn()
            return None

    def _finish(self, identifier, status, attempt, duration_ms):
        notify_attempt_usage(status,attempt,duration_ms)
        if identifier is None:
            return
        try:
            with self._connect() as connection:
                connection.execute('UPDATE attempts SET finished_at=?,status=?,actual_model=?,output_chars=?,'
                    'input_tokens=?,cached_input_tokens=?,output_tokens=?,reasoning_output_tokens=?,duration_ms=? WHERE id=?',
                    (_now(), status, attempt.actual_model, _count(attempt.output_chars),
                     *(attempt.values[key] for key in _FIELDS), duration_ms, identifier))
        except Exception:
            _warn()

    @contextmanager
    def attempt(self, *, task, provider, model=None, request_chars=None, selection_basis=None):
        started = time.monotonic()
        attempt = _Attempt()
        identifier = self._start(task, provider, model, request_chars, selection_basis)
        status = 'failed'
        try:
            yield attempt
            status = 'succeeded'
        finally:
            self._finish(identifier, status, attempt, max(0, round((time.monotonic()-started)*1000)))


@contextmanager
def usage_attempt(ledger, **kwargs):
    """Disabled accounting does no I/O and retains normal backend semantics."""
    if ledger is None:
        yield _Attempt()
    else:
        with ledger.attempt(**kwargs) as attempt:
            yield attempt


def _boundary(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('usage time boundary must be ISO 8601 text')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('usage time boundary must be ISO 8601 text') from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _aggregate_sql():
    counts = ["COUNT(*) AS attempts", "SUM(status='in_progress') AS in_progress",
              "SUM(status='failed') AS failed", "SUM(status='succeeded') AS succeeded",
              "SUM(input_tokens IS NOT NULL AND output_tokens IS NOT NULL) AS known_attempts",
              "SUM(input_tokens IS NULL OR output_tokens IS NULL) AS unknown_attempts"]
    for key in _FIELDS:
        counts.extend((f'SUM({key}) AS {key}', f'COUNT({key}) AS {key}_known_attempts'))
    for label, expression, known in (
        ('total_tokens', 'input_tokens+output_tokens',
         'input_tokens IS NOT NULL AND output_tokens IS NOT NULL'),
        ('uncached_input_tokens', 'input_tokens-cached_input_tokens',
         'input_tokens IS NOT NULL AND cached_input_tokens IS NOT NULL'),
    ):
        counts.extend((f'SUM(CASE WHEN {known} THEN {expression} END) AS {label}',
                       f'SUM({known}) AS {label}_known_attempts'))
    return ','.join(counts)


def _summary(row):
    result = dict(row)
    for key, value in result.items():
        if value is None and (key.endswith('_attempts') or key in ('attempts', 'in_progress', 'failed', 'succeeded')):
            result[key] = 0
    return result


def _groups(connection, columns, clause, args):
    groups = ','.join(columns)
    rows = connection.execute('SELECT '+groups+','+_aggregate_sql()+' FROM attempts'+clause+
        ' GROUP BY '+groups+' ORDER BY attempts DESC,'+groups+' LIMIT 50', args).fetchall()
    count = connection.execute('SELECT COUNT(*) FROM (SELECT 1 FROM attempts'+clause+
        ' GROUP BY '+groups+')', args).fetchone()[0]
    return [_summary(row) for row in rows], max(0, count-50)


def _report(connection, since, until, trace_id=None):
    columns = {row[1] for row in connection.execute('PRAGMA table_info(attempts)')}
    if trace_id is not None and 'trace_id' not in columns:
        return dict(status='unavailable', message='Legacy ledger has no query correlation; usage is unknown.')
    where, args = [], []
    if trace_id is not None:
        where.append('trace_id = ?')
        args.append(trace_id)
    for operator, boundary in (('>=', since), ('<', until)):
        if boundary is not None:
            where.append('started_at ' + operator + ' ?')
            args.append(boundary)
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    aggregate = _aggregate_sql()
    totals = _summary(connection.execute('SELECT '+aggregate+' FROM attempts'+clause, args).fetchone())
    duration = 'duration_ms' if 'duration_ms' in columns else 'NULL'
    totals.update(dict(connection.execute('SELECT SUM('+duration+') AS duration_ms, COUNT('+duration+
        ') AS duration_ms_known_attempts FROM attempts'+clause, args).fetchone()))
    tasks, tasks_omitted = _groups(connection, ('task',), clause, args)
    model_columns = ('provider','requested_model','actual_model')
    if 'selection_basis' in columns:
        model_columns += ('selection_basis',)
    models, models_omitted = _groups(connection, model_columns, clause, args)
    for group in models:
        group.setdefault('selection_basis', None)
    today = datetime.now(timezone.utc).date()
    day_start = (today-timedelta(days=30)).isoformat()
    day_end = (today+timedelta(days=1)).isoformat()
    daily_clause = clause + (' AND ' if clause else ' WHERE ') + 'started_at>=? AND started_at<?'
    daily = connection.execute("SELECT substr(started_at,1,10) AS day,"+aggregate+
        ' FROM attempts'+daily_clause+' GROUP BY day ORDER BY day LIMIT 31', [*args, day_start, day_end]).fetchall()
    tracking_start = connection.execute('SELECT started_at FROM tracking WHERE id=1').fetchone()[0]
    return dict(status='available', tracking_started_at=tracking_start, since=since, until=until,
                scope=('query generation provider attempts' if trace_id else 'database-wide generation provider attempts')+'; embeddings excluded',
                duration_semantics='Sum of provider attempt elapsed milliseconds; concurrent attempts overlap. This is not query wall time.',
                historical_usage='Unknown before tracking began; no historical reconstruction.',
                totals=totals, by_task=tasks, tasks_omitted=tasks_omitted,
                tasks_truncated=tasks_omitted>0, by_model=models, models_omitted=models_omitted,
                models_truncated=models_omitted>0, daily=[_summary(row) for row in daily],
                daily_omitted_attempts=totals['attempts']-sum(row['attempts'] for row in daily),
                daily_window_start=day_start, daily_window_days=31,
                model_semantics='Requested model and selection basis describe configuration, not execution proof. '
                                'Actual model is provider-reported only; missing model or revision remains unknown.',
                token_semantics='Cached input is included in input; reasoning output is included in output. '
                                'Total tokens use only attempts with both input and output known; uncached input '
                                'uses only attempts with both input and cached input known. Each has its own coverage count. '
                                'Nullable counts mean unknown, never assumed zero. Local response cache hits make no provider attempt.')


def read_usage(memory_db, since=None, until=None, *, trace_id=None):
    """Read-only reporting; never creates a sidecar or changes a knowledge DB."""
    if trace_id is not None:
        try:
            trace_id = str(uuid.UUID(trace_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError('trace_id must be a query trace UUID') from error
    since, until = _boundary(since), _boundary(until)
    if since is not None and until is not None and since >= until:
        raise ValueError('usage since must precede until (exclusive)')
    path = Path(str(memory_db) + '.model-usage.sqlite3')
    if not path.exists():
        return dict(status='not_tracked', tracking_started_at=None,
                    scope='database-wide generation provider attempts; embeddings excluded',
                    historical_usage='No recorded usage; historical consumption is unknown.')
    try:
        connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=0.1)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            return _report(connection, since, until, trace_id)
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return dict(status='unavailable', tracking_started_at=None,
                    message='Usage sidecar could not be read; token consumption is unknown.')


def usage_status(memory_db):
    """Bounded on-demand diagnostics for the database's own generation work."""
    from .service import _fits
    result = {'model_usage': read_usage(memory_db), 'report_kind': 'bounded_summary',
              'response_complete': True, 'summary_omissions': {},
              'detail_notice': 'Totals retain the full accounting window. Detail rows may be bounded; '
                               'summary_omissions reports any removed rows. The local usage CLI provides details.'}
    report = result['model_usage']
    while not _fits(result):
        groups = [key for key in ('by_task', 'by_model', 'daily') if report.get(key)]
        if not groups:
            raise ValueError('usage scalar metadata exceeds the response budget')
        key = max(groups, key=lambda name: len(json.dumps(report[name])))
        report[key].pop()
        result['summary_omissions'][key] = result['summary_omissions'].get(key, 0)+1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--since')
    parser.add_argument('--until')
    args = parser.parse_args()
    try:
        report = read_usage(args.db, args.since, args.until)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
