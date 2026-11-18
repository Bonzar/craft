"""Explicit canonical cutover; legacy tables remain read-only migration history."""
from .thought_store import ThoughtStore
from .thought_store_migration import migrate_batch, migration_status


def enabled(store):
    with store.transaction() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_thought_runtime'").fetchone()
        return bool(exists and db.execute('SELECT 1 FROM memory_thought_runtime WHERE singleton=1').fetchone())


def activate(store):
    thoughts = ThoughtStore(store)
    if not migration_status(thoughts)['complete']:
        raise ValueError('bounded thought migration must finish before cutover')
    with store.transaction(write=True) as db:
        db.execute('CREATE TABLE IF NOT EXISTS memory_thought_runtime (singleton INTEGER PRIMARY KEY CHECK(singleton=1))')
        db.execute('INSERT OR IGNORE INTO memory_thought_runtime VALUES(1)')
    return thoughts


def initialize_empty(store):
    if enabled(store):
        return True
    with store.transaction() as db:
        has_content = db.execute('SELECT 1 FROM memory_assertions LIMIT 1').fetchone()
        has_views = db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_views'").fetchone()
        has_content = has_content or (has_views and db.execute('SELECT 1 FROM memory_views LIMIT 1').fetchone())
    if has_content:
        return False
    thoughts = ThoughtStore(store)
    while not migrate_batch(thoughts)['complete']:
        pass
    activate(store)
    return True
