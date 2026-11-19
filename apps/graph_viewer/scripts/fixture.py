"""Stream an explicitly synthetic source-native fixture into a NEW directory.

Uses only stdlib and copied public source table schemas. It never constructs the
source MemoryStore/Engine, calls a model, or accepts an existing database path.
Small fixtures exercise the harness; only the default full size qualifies scale.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time

JAN1 = '2026-01-01T00:00:00.000000+00:00'
JAN15 = '2026-01-15T00:00:00.000000+00:00'
MAR1 = '2026-03-01T00:00:00.000000+00:00'
SCOPE = 'viewer-qa-synthetic'
SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE memory_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version INTEGER NOT NULL);
INSERT INTO memory_state VALUES(1,0);
CREATE TABLE memory_assertions(assertion_id TEXT PRIMARY KEY NOT NULL,text TEXT NOT NULL,actor TEXT NOT NULL,source TEXT NOT NULL,episode TEXT NOT NULL,valid_at TEXT,recorded_at TEXT NOT NULL,scope TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX memory_assertion_scope_time ON memory_assertions(scope,valid_at,recorded_at);
CREATE TABLE memory_edges(edge_id TEXT PRIMARY KEY NOT NULL,from_id TEXT NOT NULL REFERENCES memory_assertions(assertion_id),to_id TEXT NOT NULL REFERENCES memory_assertions(assertion_id),kind TEXT NOT NULL,recorded_at TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX memory_edge_from ON memory_edges(from_id);
CREATE INDEX memory_edge_to ON memory_edges(to_id);
CREATE VIRTUAL TABLE memory_fts USING fts5(text,content='memory_assertions',content_rowid='rowid');
CREATE TRIGGER memory_assertion_insert AFTER INSERT ON memory_assertions BEGIN
 INSERT INTO memory_fts(rowid,text) VALUES(new.rowid,new.text);
 UPDATE memory_state SET version=version+1 WHERE singleton=1;
END;
CREATE TRIGGER memory_edge_insert AFTER INSERT ON memory_edges BEGIN UPDATE memory_state SET version=version+1 WHERE singleton=1; END;
CREATE TABLE memory_raw_events(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,turn_id TEXT NOT NULL,prompt TEXT NOT NULL,actor TEXT NOT NULL,source TEXT NOT NULL,scope TEXT NOT NULL,recorded_at TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE TABLE memory_event_positions(event_id TEXT PRIMARY KEY REFERENCES memory_raw_events(id),position INTEGER NOT NULL CHECK(position>=0));
CREATE TABLE memory_views(id TEXT PRIMARY KEY,definition TEXT NOT NULL,current_id INTEGER,checked_version INTEGER,error TEXT,current_check_id INTEGER);
CREATE TABLE memory_view_versions(id INTEGER PRIMARY KEY,view_id TEXT NOT NULL REFERENCES memory_views(id),fingerprint TEXT NOT NULL,snapshot TEXT NOT NULL,material_fingerprint TEXT,UNIQUE(view_id,fingerprint));
CREATE TABLE memory_view_checks(id INTEGER PRIMARY KEY,view_id TEXT NOT NULL REFERENCES memory_views(id),version_id INTEGER NOT NULL REFERENCES memory_view_versions(id),source_version INTEGER NOT NULL,coverage_version INTEGER NOT NULL,valid_cutoff TEXT NOT NULL,recorded_cutoff TEXT NOT NULL,pipeline_key TEXT NOT NULL,input_fingerprint TEXT NOT NULL,checked_at TEXT NOT NULL,overview_review TEXT,query_snapshot TEXT,freshness TEXT);
CREATE TABLE memory_question_routes(request_id TEXT PRIMARY KEY,view_id TEXT NOT NULL REFERENCES memory_views(id),plan TEXT NOT NULL);
CREATE TABLE memory_question_route_versions(request_id TEXT NOT NULL,revision INTEGER NOT NULL,view_id TEXT NOT NULL REFERENCES memory_views(id),plan TEXT NOT NULL,recorded_at TEXT,PRIMARY KEY(request_id,revision));
'''


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def node_id(index: int) -> str:
    return f'qa-node-{index:08d}'


def edge_id(index: int) -> str:
    return f'qa-edge-{index:08d}'


CASE_INDEX = {
    'long_assertion': 0, 'semantic_paraphrase': 1, 'unicode_target': 2,
    'undated_requirement': 3, 'future_plan': 4, 'late_fact': 5,
    'late_interpretation': 6, 'late_available': 7, 'future_old': 8,
    'future_replacement': 9, 'superseded_old': 10, 'replacement': 11,
    'conditional_old': 12, 'conditional_new': 13, 'cross_scope_old': 14,
    'cross_scope_new': 15, 'proposal_old': 16, 'proposal_new': 17,
    'inference_old': 18, 'inference_new': 19, 'cycle_a': 20, 'cycle_b': 21,
    'conflict_a': 22, 'conflict_b': 23, 'explicit_cancelled': 24,
    'missing_source': 25, 'partial_source': 26, 'unaffiliated_sentinel': 27,
    'invalid_source_span': 28, 'literal_html': 29, 'day_precision': 30,
    'unknown_precision': 31,
}
SPECIAL_EDGES = [
    (9, 8, 'supersedes', {}), (11, 10, 'supersedes', {}),
    (13, 12, 'supersedes', {'condition': 'Только после отдельного согласования'}),
    (15, 14, 'supersedes', {}), (17, 16, 'supersedes', {}),
    (19, 18, 'supersedes', {}), (20, 21, 'supersedes', {}),
    (21, 20, 'supersedes', {}), (22, 23, 'contradicts', {'basis': 'observation'}),
    (0, 2, 'supports', {'source_quote': 'точный фрагмент 🧭 e\u0301', 'target_quote': 'повтор 🪐'}),
    (2, 1, 'supports', {'source_quote': 'уникальное окончание 🛰️', 'target_quote': 'железнодорожные поездки'}),
    (0, 25, 'depends_on', {}),
]


def assertion(index: int, deep_ref: dict) -> tuple:
    key = node_id(index)
    text = f'Синтетическое утверждение {index:08d}. Контрольная запись графа; значение {index % 997}.'
    metadata = {'fixture': 'graph-viewer-qa-v1', 'kind': 'assertion', 'temporal_precision': 'day'}
    valid, recorded, scope = JAN1, JAN1, SCOPE
    if index == 0 or index >= 32 and index % 5000 == 0:
        text = (f'НАЧАЛО {key}\n' + ('Длинный абзац 🛰️ о чтении источника, переносах и сохранении позиции.\n' * 1800)
                + 'точный фрагмент 🧭 e\u0301\n' + f'КОНЕЦ {key}')
        metadata.update(event_id=deep_ref['record_id'], evidence_refs=[deep_ref], source_span={'start': deep_ref['start'], 'end': deep_ref['end']}, context_event_ids=['qa-context-before', 'qa-context-after'])
    elif index == 1:
        text = 'Сотрудники выбирают железнодорожные поездки: так служебные перемещения оставляют меньший углеродный след.'
    elif index == 2:
        text = '🧭 e\u0301 повтор 🪐 и ещё повтор 🪐; уникальное окончание 🛰️'
    elif index == 3:
        text, valid = 'Требование: все эвакуационные выходы должны оставаться свободными.', None
    elif index == 4:
        text, valid, metadata['kind'] = 'План: открыть новую лабораторию с первого марта 2027 года.', '2027-03-01T00:00:00.000000+00:00', 'proposal'
    elif index == 5:
        text, valid, recorded = 'Четвёртого января проведена проверка вентиляции.', '2026-01-04T00:00:00.000000+00:00', JAN15
    elif index == 6:
        text = 'Поздно интерпретированное сообщение о четвёртом января.'
        metadata['interpretation'] = {'interpreted_at': JAN15}
    elif index == 7:
        text = 'Сведения стали доступны источнику позднее даты регистрации.'
        metadata['source_available_at'] = JAN15
    elif index in {8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 21}:
        text = f'Решение: контрольное правило варианта {index}.'
        metadata['kind'] = 'decision'
        if index in {9, 11, 13, 15}:
            recorded = JAN15
        if index == 9:
            valid = MAR1
        if index == 11:
            valid = JAN15
        if index == 15:
            scope += '-other'
    elif index == 17:
        text, metadata['kind'] = 'Предложение: заменить ранее принятое решение.', 'proposal'
    elif index == 19:
        text, metadata['kind'] = 'Вывод: возможно стоит заменить правило.', 'inference'
    elif index in {22, 23}:
        text = ('Наблюдение: аудитория рассчитана на сорок мест.' if index == 22 else 'Наблюдение: аудитория рассчитана на пятьдесят мест.')
        metadata['kind'] = 'observation'
    elif index == 24:
        text = 'Явно отменённая запись синтетического провайдера.'
        metadata['viewer_status'] = {'disposition': 'cancelled', 'explicit': True, 'published': True}
    elif index == 25:
        text = 'Исходный фрагмент отсутствует в сохранённых событиях.'
        metadata['evidence_refs'] = [{'event_id': 'qa-event-not-saved', 'start': 0, 'end': 5}]
    elif index == 26:
        text = 'Сохранилась только часть внешнего источника.'
        metadata.update(event_id='qa-partial-source', context_truncated=True,
                        evidence_refs=[{'event_id': 'qa-partial-source', 'start': 0, 'end': 21}])
    elif index == 27:
        text = 'ДАЛЁКИЙ УЗЕЛ ВСЕЙ ВСЕЛЕННОЙ: отсутствует в любой вопросной подборке.'
    elif index == 28:
        metadata['evidence_refs'] = [{'event_id': deep_ref['record_id'], 'start': 9000000, 'end': 9000010}]
    elif index == 29:
        text = '<img src=x onerror="window.__fixtureXss=true"> javascript:alert(1) & буквальный текст.'
    elif index == 30:
        text, valid = 'Событие известно только с точностью до дня.', '2026-01-04T00:00:00.000000+00:00'
    elif index == 31:
        metadata.pop('temporal_precision')
        metadata['actor_unknown'] = True
    return key, text, '' if index == 31 else 'synthetic-vlad', 'synthetic-fixture', 'qa-session', valid, recorded, scope, encode(metadata)


def batches(rows, size=1000):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def create_topics(db, nodes: int, version: int) -> dict:
    definitions = [
        ('qa-universe', 'Синтетическая вселенная', 'Какие сведения есть в контрольной базе?', ['qa-building', 'qa-travel']),
        ('qa-building', 'Здание и безопасность', 'Что известно о здании и безопасности?', ['qa-shared']),
        ('qa-travel', 'Поездки и инфраструктура', 'Что известно о поездках и инфраструктуре?', ['qa-shared']),
        ('qa-shared', 'Безопасные перемещения', 'Что известно о безопасных перемещениях?', []),
        ('qa-small', 'Редкая малая тема', 'Что известно о редкой малой теме?', []),
        ('qa-singleton', 'Одиночная тема', 'Что известно об одиночной теме?', []),
    ]
    query_by_id = {x[0]: x[2] for x in definitions}
    memberships = {}
    for number, (key, title, query, deps) in enumerate(definitions, 1):
        if key == 'qa-universe':
            member_ids = [node_id(i) for i in range(nodes) if i != 27]
        elif key == 'qa-building':
            member_ids = [node_id(i) for i in range(nodes) if i % 3 != 1 and i != 27]
        elif key == 'qa-travel':
            member_ids = [node_id(i) for i in range(nodes) if i % 3 == 1 or i in {0, 2}]
        elif key == 'qa-shared':
            member_ids = [node_id(0), node_id(2)]
        elif key == 'qa-small':
            member_ids = [node_id(i) for i in range(22, 25)]
        else:
            member_ids = [node_id(nodes-1)]
        definition = {'id': key, 'title': title, 'query': query, 'scopes': [SCOPE], 'root_ids': [], 'depends_on': deps, 'max_seeds': 8}
        snapshot = {'id': number, 'view_id': key, 'summary': title, 'evidence': [{'id': item} for item in member_ids], 'edges': [], 'source_version': version, 'created_at': JAN1, 'valid_as_of': None, 'recorded_as_of': None, 'upstream': {}, 'missing_roots': []}
        plan = {'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [{'question': query_by_id[child], 'purpose': 'domain_evidence'} for child in deps], 'reason': 'Explicit synthetic source plan, not viewer inference.'}
        db.execute('INSERT INTO memory_views VALUES(?,?,?,?,?,?)', (key, encode(definition), number, version, None, number))
        encoded_snapshot = encode(snapshot)
        digest = hashlib.sha256(encoded_snapshot.encode()).hexdigest()
        db.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?,?)', (number, key, digest, encoded_snapshot, digest))
        db.execute('INSERT INTO memory_view_checks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (number, key, number, version, version, MAR1, MAR1, 'qa-fixture', digest, JAN1, None, None, encode({'covered_corpus_revision': version})))
        db.execute('INSERT INTO memory_question_routes VALUES(?,?,?)', (key, key, encode(plan)))
        db.execute('INSERT INTO memory_question_route_versions VALUES(?,?,?,?,?)', (key, 1, key, encode(plan), JAN1))
        memberships[key] = {'members': len(member_ids), 'parents': [parent[0] for parent in definitions if key in parent[3]]}
    return memberships


def table_digest(db, table: str, columns: str, order: str) -> str:
    digest = hashlib.sha256()
    for row in db.execute(f'SELECT {columns} FROM {table} ORDER BY {order}'):
        digest.update(encode(row).encode())
        digest.update(b'\n')
    return digest.hexdigest()


def generate_fixture(directory: Path, *, nodes: int = 100_000, edges: int = 1_000_000) -> dict:
    if isinstance(nodes, bool) or isinstance(edges, bool) or nodes < 64 or edges < len(SPECIAL_EDGES):
        raise ValueError('Fixture requires >=64 nodes and enough edges for all source cases')
    directory = Path(directory).expanduser().absolute()
    # Refuse an existing directory, including real source paths; output is NEW only.
    directory.mkdir(parents=True, exist_ok=False)
    (directory / '.viewer-qa-owned').write_text('SYNTHETIC graph-viewer fixture only\n')
    database = directory / 'source.sqlite3'
    started = time.monotonic()
    quote = 'точный фрагмент 🧭 e\u0301'
    prefix = 'Контекст до фрагмента 🛰️\n' * 1200
    source_text = prefix + quote + '\n' + ('Контекст после фрагмента.\n' * 1800) + 'КОНЕЦ СОХРАНЁННОГО ИСТОЧНИКА'
    deep_ref = {'event_id': 'qa-deep-unicode-source', 'record_id': 'qa-deep-unicode-source', 'start': len(prefix), 'end': len(prefix) + len(quote), 'quote': quote}
    db = sqlite3.connect(database)
    try:
        db.executescript(SCHEMA)
        db.execute('PRAGMA journal_mode=WAL')
        for ordinal, (key, text, extra) in enumerate([
            ('qa-context-before', 'Предыдущее сохранённое сообщение с контекстом.', {}),
            (deep_ref['record_id'], source_text, {'external_source_complete': True}),
            ('qa-context-after', 'Следующее сохранённое сообщение с контекстом.', {}),
            ('qa-partial-source', 'Сохранённый фрагмент. Дальнейшая часть не сохранена.', {'external_source_complete': False}),
        ]):
            db.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)', (key, 'qa-session', str(ordinal), text, 'synthetic-vlad', 'synthetic-fixture', SCOPE, JAN1, encode(extra)))
            db.execute('INSERT INTO memory_event_positions VALUES(?,?)', (key, ordinal))
        for batch in batches(assertion(i, deep_ref) for i in range(nodes)):
            db.executemany('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)', batch)
            db.commit()
        def edge_rows():
            for i in range(edges):
                if i < len(SPECIAL_EDGES):
                    origin, target, kind, metadata = SPECIAL_EDGES[i]
                    recorded = JAN15 if i in {0, 1, 2, 3} else JAN1
                elif i < 2012:
                    # High-degree star exercises >200 adjacency cursor and all highlighting.
                    origin, target, kind, metadata, recorded = 0, 32 + (i % (nodes-32)), 'supports', {}, JAN1
                else:
                    offset = i - 2012
                    origin = 32 + (offset // 10) % (nodes-32)
                    target = 32 + ((offset // 10 + 1 + offset % 10 * 7919) % (nodes-32))
                    if target == origin:
                        target = 32 + ((target - 31) % (nodes-32))
                    kind, metadata, recorded = 'supports', {}, JAN1
                yield edge_id(i), node_id(origin), node_id(target), kind, recorded, encode(metadata)
        for batch in batches(edge_rows()):
            db.executemany('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)', batch)
            db.commit()
        version = db.execute('SELECT version FROM memory_state').fetchone()[0]
        topics = create_topics(db, nodes, version)
        db.commit()
        counts = {'nodes': db.execute('SELECT count(*) FROM memory_assertions').fetchone()[0], 'edges': db.execute('SELECT count(*) FROM memory_edges').fetchone()[0]}
        if counts != {'nodes': nodes, 'edges': edges} or db.execute('PRAGMA foreign_key_check').fetchone():
            raise AssertionError('Fixture integrity failed')
        digests = {'assertions': table_digest(db, 'memory_assertions', '*', 'assertion_id'), 'edges': table_digest(db, 'memory_edges', '*', 'edge_id'), 'topics': table_digest(db, 'memory_view_versions', '*', 'id')}
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        db.close()
    manifest = {'schema': 1, 'synthetic': True, 'fixture_id': 'graph-viewer-qa-v1', 'database': str(database), 'counts': counts,
                'scale_qualified': nodes == 100000 and edges == 1000000, 'digests': digests, 'first_id': node_id(0), 'last_id': node_id(nodes-1),
                'cases': {key: node_id(index) for key, index in CASE_INDEX.items()}, 'special_edges': {str(i): edge_id(i) for i in range(len(SPECIAL_EDGES))},
                'sources': {'deep_unicode': deep_ref}, 'topics': topics,
                'semantic_search': {'query': 'Как уменьшить климатический вред командировок?', 'relevant_id': node_id(1), 'no_test_lookup_allowed': True},
                'temporal': {'early': '2026-01-05T23:59:59.999999+00:00', 'before_effective': '2026-02-01T23:59:59.999999+00:00', 'after_effective': '2026-03-02T23:59:59.999999+00:00'},
                'generation_seconds': round(time.monotonic()-started, 3), 'database_bytes': database.stat().st_size,
                'limitations': ['Metadata viewer_status explicit cancellation requires source provider support; absence is acceptance gap.', 'Fixture annotations are a relevance oracle only, never search implementation.']}
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='New synthetic-only output directory; existing directory is refused')
    parser.add_argument('--nodes', type=int, default=100000)
    parser.add_argument('--edges', type=int, default=1000000)
    args = parser.parse_args()
    manifest = generate_fixture(args.directory, nodes=args.nodes, edges=args.edges)
    print(json.dumps({key: manifest[key] for key in ('database','counts','scale_qualified','digests','generation_seconds','database_bytes')}, indent=2))


if __name__ == '__main__':
    main()
