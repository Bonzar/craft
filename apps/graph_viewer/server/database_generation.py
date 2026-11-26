"""Read the reset fence from the exact live read-only database, never its salt."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import re
import sqlite3

LEGACY = '0' * 32

def database_generation(path):
    path = Path(path)
    before = path.stat()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.2)) as db:
        db.execute('BEGIN')
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_database_clear_state'").fetchone()
        if table:
            rows = db.execute('SELECT revision FROM memory_database_clear_state WHERE singleton=1').fetchall()
            if len(rows)!=1 or not isinstance(rows[0][0],str) or not re.fullmatch('[a-f0-9]{32}',rows[0][0]):
                raise ValueError('Bad database generation')
            generation=rows[0][0]
        else:
            generation=LEGACY
    after = path.stat()
    if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):
        raise ValueError('Database identity changed during reset fence read')
    return after.st_dev,after.st_ino,generation


def epoch_name(generation):
    if not isinstance(generation,tuple) or len(generation)!=3:
        raise ValueError('invalid graph cache epoch')
    return hashlib.sha256(json.dumps(generation).encode()).hexdigest()
