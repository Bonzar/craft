"""Legacy duplicate bytes compact only against preserved canonical proof."""
import json
import unittest
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_store_migration import migrate_batch
from prototypes.assertion_memory.thought_legacy_compaction import compact_legacy_batch, resolve_legacy_snapshot


class ThoughtLegacyCompactionTest(unittest.TestCase):
    def test_exact_canonical_history_remains_readable_and_unmatched_or_unfinished_data_stays(self):
        with MemoryStore(':memory:') as memory:
            queue = CaptureQueue(memory)
            event = queue.capture_event('s','chat','Useful choice.',source='fixture')
            pending = queue.capture_event('s','pending','Work remains.',source='fixture')
            memory.append_assertion(assertion_id='fact',text='Useful choice.',actor='user',source='fixture',episode='s',recorded_at='2026-01-01')
            engine = MemoryEngine(memory)
            engine.define_view(ViewSpec('view','Useful view','Useful question?',root_ids=('fact',)))
            old = engine.build_view('view')
            thoughts = ThoughtStore(memory)
            while not migrate_batch(thoughts,limit=5)['complete']:
                pass
            canonical = thoughts.get('view')
            original = json.loads(canonical.metadata['legacy']['original']['snapshot'])
            engine.define_view(ViewSpec('unmatched','Unmatched','Later question?',root_ids=('fact',)))
            unmatched = engine.build_view('unmatched')
            with memory.transaction(write=True) as c:
                untouched = c.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(unmatched.id,)).fetchone()[0]
                c.execute("UPDATE memory_capture_jobs SET status='completed',candidates='[1]',proposals='[2]',refreshed_views='{\"v\":1}',extracted_ids='[\"fact\"]' WHERE id=?",(event.id,))
                c.execute("UPDATE memory_capture_jobs SET candidates='[3]',proposals='[4]' WHERE id=?",(pending.id,))
            result = compact_legacy_batch(thoughts,limit=1)
            while not result['complete']:
                self.assertLessEqual(result['processed'],1)
                result = compact_legacy_batch(thoughts,limit=1)
            self.assertEqual(thoughts.get('view'),canonical)
            self.assertEqual(resolve_legacy_snapshot(thoughts,old.id),original)
            with memory.transaction() as c:
                receipt = json.loads(c.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(old.id,)).fetchone()[0])
                self.assertEqual(receipt['_format'],'canonical-thought-reference/v1')
                self.assertEqual(c.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(unmatched.id,)).fetchone()[0],untouched)
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(queue.get(event.id).extracted_ids,['fact'])
            self.assertEqual(queue.get(event.id).candidates,[])
            self.assertEqual(queue.get(event.id).refreshed_views,{})
            self.assertEqual(queue.get(pending.id).candidates,[3])
            self.assertTrue(result['protected'])


if __name__ == '__main__':
    unittest.main()
