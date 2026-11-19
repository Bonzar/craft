"""Fixture preconditions are independent of viewer behavior/performance claims."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'fixture.py'
spec = importlib.util.spec_from_file_location('graph_viewer_fixture', SCRIPT)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class FixtureTests(unittest.TestCase):
    def test_complete_graph_has_valid_unique_edges_and_explicit_source_topics(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture.generate_fixture(Path(temporary) / 'synthetic', nodes=240, edges=2400)
            with sqlite3.connect(manifest['database']) as db:
                self.assertEqual(db.execute('select count(*) from memory_assertions').fetchone()[0], 240)
                self.assertEqual(db.execute('select count(*) from memory_edges').fetchone()[0], 2400)
                self.assertEqual(db.execute('pragma foreign_key_check').fetchall(), [])
                self.assertEqual(db.execute('select count(distinct edge_id) from memory_edges').fetchone()[0], 2400)
                plans = [json.loads(row[0]) for row in db.execute('select plan from memory_question_route_versions')]
                self.assertGreaterEqual(sum(bool(p['subquestions']) for p in plans), 2)
                self.assertEqual(manifest['synthetic'], True)
                self.assertEqual(manifest['counts'], {'nodes': 240, 'edges': 2400})

    def test_temporal_and_unicode_oracles_are_source_values_with_real_offsets(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = fixture.generate_fixture(Path(temporary) / 'synthetic', nodes=80, edges=120)
            with sqlite3.connect(manifest['database']) as db:
                records = {row[0]: row[1:] for row in db.execute('select assertion_id,valid_at,recorded_at,metadata from memory_assertions')}
                self.assertIsNone(records[manifest['cases']['undated_requirement']][0])
                self.assertGreater(records[manifest['cases']['future_plan']][0], '2026-11-11')
                focus = manifest['sources']['deep_unicode']
                text = db.execute('select prompt from memory_raw_events where id=?', (focus['record_id'],)).fetchone()[0]
                self.assertGreater(focus['start'], 12000)
                self.assertEqual(text[focus['start']:focus['end']], focus['quote'])
                self.assertNotEqual(len(text[:focus['start']].encode('utf-16-le')) // 2, focus['start'])
                self.assertIn('source_available_at', json.loads(records[manifest['cases']['late_available']][2]))
                metadata = json.loads(records[manifest['cases']['long_assertion']][2])
                self.assertEqual(metadata['source_span'], {'start': focus['start'], 'end': focus['end']})
                cancelled = json.loads(records[manifest['cases']['explicit_cancelled']][2])
                self.assertIs(cancelled['viewer_status'].get('published'), True)

    def test_existing_output_is_never_overwritten_and_generation_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = fixture.generate_fixture(Path(temporary) / 'a', nodes=80, edges=120)
            second = fixture.generate_fixture(Path(temporary) / 'b', nodes=80, edges=120)
            self.assertEqual(first['digests'], second['digests'])
            with self.assertRaises(FileExistsError):
                fixture.generate_fixture(Path(temporary) / 'a', nodes=80, edges=120)

    def test_control_size_cannot_be_claimed_by_invalid_tiny_or_dangling_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                fixture.generate_fixture(Path(temporary) / 'bad', nodes=20, edges=2)


if __name__ == '__main__':
    unittest.main()
