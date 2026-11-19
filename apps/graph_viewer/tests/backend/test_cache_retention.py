import json
import os
import tempfile
import unittest
from pathlib import Path
from apps.graph_viewer.server.cache_retention import prune_cache


def test_retention_preserves_current_recent_unknown_and_source(tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    for i in range(6):
        d = cache / f'{i:032x}'
        d.mkdir()
        f = d / 'manifest.json'
        f.write_text(json.dumps({'schema': 1, 'revision': d.name, 'selector': {}, 'provider_id': 'test'}))
        os.utime(f, (i, i))
    (cache / f'{1:032x}' / 'unknown').write_text('keep')
    source = tmp_path / 'viewer.sqlite3'
    source.write_text('user state')
    graph_paths = {cache / f'{0:032x}'}
    prune_cache(cache, graph_paths, clock=10000)
    assert {d.name for d in cache.iterdir()} == {f'{i:032x}' for i in (0, 1, 4, 5)}
    assert source.read_text() == 'user state'


class CacheRetentionContract(unittest.TestCase):
    def test_safe_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            test_retention_preserves_current_recent_unknown_and_source(Path(directory))

    def test_source_ir_preserves_current_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            provider=state/'source-ir'/('a'*64)
            provider.mkdir(parents=True)
            for i in range(4):
                d=provider/f'{i:064x}';d.mkdir()
                f=d/'source.sqlite3';f.write_text('derived')
                os.utime(f,(i,i))
            current=provider/f'{0:064x}'
            prune_cache(state/'cache',{current},clock=10000)
            self.assertTrue(current.exists())
            self.assertFalse((provider/f'{1:064x}').exists())
            self.assertEqual(len(list(provider.iterdir())),3)
