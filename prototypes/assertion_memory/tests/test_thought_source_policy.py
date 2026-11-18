"""Auto Codex visibility is a provenance rule, never a semantic model decision."""
import unittest
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import Thought, ThoughtStore
from prototypes.assertion_memory.thought_selection import ThoughtSelection
from prototypes.assertion_memory.thought_source_policy import event_eligibility, thought_eligibility, cleanup_batch


class ThoughtSourcePolicyTest(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryStore(':memory:')
        self.addCleanup(self.memory.close)
        self.queue = CaptureQueue(self.memory)
        self.store = ThoughtStore(self.memory)

    def event(self, turn, actor, source, metadata):
        return self.queue.capture_event('session', turn, 'Fixture data.', actor=actor, source=source, metadata=metadata)

    def test_only_automatic_codex_tools_and_private_channels_are_excluded(self):
        for actor, metadata in [('tool:exec_command', {}), ('assistant', {'channel': 'analysis'}),
                ('assistant', {'payload_type': 'function_call'})]:
            self.assertFalse(event_eligibility({'actor': actor, 'source': 'codex://session/a', 'metadata': metadata})['allowed'])
        for actor, metadata in [('user', {}), ('assistant', {'channel': 'final'}), ('assistant', {'channel': 'commentary'})]:
            self.assertTrue(event_eligibility({'actor': actor, 'source': 'codex://session/a', 'metadata': metadata})['allowed'])
        self.assertTrue(event_eligibility({'actor': 'tool:exec_command', 'source': 'manual://tool', 'metadata': {'role': 'tool'}})['allowed'])

    def test_tool_noise_cannot_consume_the_visible_context_budget(self):
        from types import SimpleNamespace
        from prototypes.assertion_memory.thought_worker import ThoughtWorker
        user = self.event('visible-user', 'user', 'codex://session/a/user', {'role': 'user'})
        assistant = self.event('visible-assistant', 'assistant', 'codex://session/a/assistant', {'role': 'assistant', 'channel': 'final'})
        for index in range(10):
            self.event('tool-' + str(index), 'tool:exec_command', 'codex://session/a/tool/exec', {'role': 'tool'})
        target = self.event('target', 'user', 'codex://session/a/user', {'role': 'user'})
        engine = SimpleNamespace(store=self.memory, thoughts=self.store, model=None, gateway=None)
        worker = ThoughtWorker(self.queue, engine, context_limit=2)
        self.assertEqual([row.id for row in worker._context(target)], [user.id, assistant.id])

    def test_excluded_old_tool_cannot_reappear_through_composite_history_or_scope(self):
        tool = self.event('tool', 'tool:exec_command', 'codex://session/a/tool/exec', {'role': 'tool'})
        user = self.event('chat', 'user', 'codex://session/a/user', {'role': 'user'})
        chat = self.store.publish('Visible chat fact.', thought_id='chat', bases=[{'event_id': user.id}])
        old = Thought('old-tool', 1, 'Exec artifact.', None,
            {'kind': 'legacy_assertion', 'source': tool.source, 'actor': tool.actor},
            [{'event_id': tool.id}], {}, '2026-01-01')
        mixed = Thought('mixed', 1, 'Mixed old composite.', 'Old mixed question?', {'kind': 'inference'},
            [{'thought_id': old.id, 'version': 1}, {'thought_id': chat.id, 'version': 1}], {}, '2026-01-01')
        with self.memory.transaction(write=True) as c:
            self.store._insert(c, old)
            self.store._insert(c, mixed)
        self.assertIsNone(self.store.get(old.id))
        self.assertIsNone(self.store.get(mixed.id))
        self.assertEqual(self.store.search_text('artifact composite'), [])
        self.assertEqual([row.id for row in self.store.list_thoughts()], ['chat'])
        self.assertFalse(thought_eligibility(self.store, self.store.get(old.id, 1))['allowed'])
        scoped = ThoughtSelection(self.store, scope='default')
        self.assertIsNone(scoped.get(mixed.id))
        result = cleanup_batch(self.store, limit=1)
        self.assertLessEqual(result['processed'], 1)
        while not result['complete']:
            result = cleanup_batch(self.store, limit=1)
            self.assertLessEqual(result['processed'], 1)
        self.assertEqual(self.store.get(old.id, 1), old)
        self.assertEqual(self.store.get(mixed.id, 1), mixed)
        self.assertEqual(self.queue.get(tool.id).phase, 'excluded')
        self.assertEqual(self.queue.get(tool.id).status, 'completed')
        self.assertEqual(self.queue.get(user.id).status, 'waiting_model')
        with self.assertRaises(ValueError):
            self.store.publish('Recreated exec.', bases=[{'event_id': tool.id}])


if __name__ == '__main__':
    unittest.main()
