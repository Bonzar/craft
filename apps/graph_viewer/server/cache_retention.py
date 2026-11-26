"""Portable expiry of viewer-owned derived files, never source records."""
import json
import os
from pathlib import Path
import re
import shutil
import time

GRACE_SECONDS = 3600
KEEP_GENERATIONS = 2
INTERVAL_SECONDS = 300
CACHE_FILES = {'edges.bin', 'nodes.bin.gz', 'watermarks.json', 'manifest.json',
               'semantic.json', 'nodes.bin', 'projection.sqlite3', 'edges.bin.gz',
               'vectors.f32', 'topics.json'}

def generation(directory, kind):
    if directory.is_symlink() or not directory.is_dir():
        return None
    files = list(directory.iterdir())
    allowed = CACHE_FILES if kind == 'cache' else {'source.sqlite3'}
    if not files or any(f.is_symlink() or not f.is_file() or f.name not in allowed for f in files):
        return None
    if any(f.stat().st_uid != os.getuid() for f in files):
        return None
    if kind == 'cache':
        if not re.fullmatch('[0-9a-f]{32}', directory.name):
            return None
        try:
            manifest = json.loads((directory / 'manifest.json').read_text())
        except (OSError, ValueError):
            return None
        if manifest.get('revision') != directory.name or manifest.get('schema') != 1:
            return None
        key = json.dumps(['cache', manifest.get('provider_id')])
    else:
        if not re.fullmatch('[0-9a-f]{64}', directory.name):
            return None
        key = str(directory.parent)
    return (directory, key, max(f.stat().st_mtime for f in files),
            sum(f.stat().st_size for f in files))


def candidates(state, opened, clock):
    rows = []
    cache = state / 'cache'
    ir = state / 'source-ir'
    if cache.is_symlink() or ir.is_symlink():
        raise ValueError('Symlink cache root')
    locations = [(cache, 'cache')]
    epochs=cache/'database-epochs'
    if epochs.is_symlink():
        raise ValueError('Symlink epoch cache root')
    if epochs.is_dir():
        locations.extend((p,'cache') for p in epochs.iterdir() if re.fullmatch('[0-9a-f]{64}',p.name)
                         and p.is_dir() and not p.is_symlink())
    if ir.exists():
        locations += [(p, 'source-ir') for p in ir.iterdir() if p.is_dir() and not p.is_symlink()]
    for parent, kind in locations:
        if parent.exists():
            rows += [row for d in parent.iterdir() if (row := generation(d, kind))]
    protected = set(opened)
    for path in opened:
        protected.update(path.parents)
    counts = {}
    result = []
    for row in sorted(rows, key=lambda item: item[2], reverse=True):
        directory, key, modified, size = row
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= KEEP_GENERATIONS or modified > clock - GRACE_SECONDS:
            continue
        if directory in protected:
            continue
        result.append(row)
    return result


def prune_cache(cache, protected=(), clock=None):
    state = Path(cache).parent
    rows = candidates(state, set(protected), time.time() if clock is None else clock)
    removed = 0
    for directory, key, modified, size in rows:
        try:
            shutil.rmtree(directory)
            removed += size
        except OSError:
            # A reader on platforms that disallow unlink keeps its generation.
            import logging
            logging.getLogger(__name__).warning('cache_expiry_deferred revision=%s', directory.name)
    return removed
