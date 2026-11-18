import unittest
from dataclasses import asdict
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_selection import ThoughtSelection
from prototypes.assertion_memory.thought_payload import encode_payload,decode_payload,compact_batch

class ThoughtPayloadTest(unittest.TestCase):
    def test_packing_preserves_logical_history_hashes_basis_lookup_and_selection_filter(self):
        with MemoryStore(':memory:') as memory:
            thoughts=ThoughtStore(memory)
            source=thoughts.publish('Keep the exact exception.',metadata={'long_audit':'Repeated audit. '*1000})
            parent=thoughts.publish('A conclusion with its exception.',bases=[{'thought_id':source.id,'version':source.version}])
            historical=ThoughtSelection(thoughts,recorded_as_of='2026-11-17T23:00:00+00:00')
            old=historical.publish('Historical answer.',question='Then?',metadata={'long_audit':'Repeated audit. '*1000})
            with memory.transaction() as db:
                db.execute('UPDATE memory_thought_versions SET payload=? WHERE thought_id=?',
                    (__import__('json').dumps(asdict(source)),source.id))
                before=dict(db.execute('SELECT thought_id,content_hash FROM memory_thought_versions').fetchall())
            result=compact_batch(thoughts)
            self.assertTrue(result['complete']);self.assertGreater(result['saved_bytes'],1000)
            self.assertEqual(thoughts.get(source.id),source)
            self.assertEqual(thoughts.get(parent.id),parent)
            self.assertEqual([row.id for row in thoughts.list_thoughts()],[row.id for row in sorted([source,parent],key=lambda row:row.id)])
            with memory.transaction() as db:
                after=dict(db.execute('SELECT thought_id,content_hash FROM memory_thought_versions').fetchall())
            self.assertEqual(before,after)
            decoded=decode_payload(encode_payload(asdict(old)))
            self.assertEqual(decoded,asdict(old))
            later=thoughts.publish('Another supported conclusion.',bases=parent.bases)
            self.assertEqual(later.bases,parent.bases)
