"""Automatic Codex intake admits visible chat without internal/tool history."""
from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prototypes.assertion_memory.model import SemanticUnavailable
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import Thought, ThoughtStore
from prototypes.assertion_memory.tests.test_thought_engine import ACCEPT, ScriptedModel, draft


class ThoughtAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'admission.sqlite3')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)
        self.queue = CaptureQueue(self.memory)

    def test_visible_assistant_keeps_report_confidence_and_excludes_auto_internal_context(self):
        tool = self.queue.capture_event('fixture', 'tool', 'SECRET_AUTOMATIC_TOOL_RESULT',
            source='codex://fixture', actor='tool', metadata={'role': 'tool'})
        analysis = replace(tool, actor='assistant', prompt='SECRET_INTERNAL_REASONING',
                           metadata={'role': 'assistant', 'channel': 'analysis'})
        unused = ScriptedModel()
        engine = ThoughtEngine(self.store, unused)
        self.assertEqual(engine.ingest(tool), [])
        self.assertEqual(engine.reprocess_source(analysis), {'thoughts': [], 'retired': []})
        visible = self.queue.capture_event('fixture', 'visible', 'I ran the gate test and it passed.',
            source='codex://fixture', actor='assistant', metadata={'role': 'assistant', 'channel': 'final'})
        model = ScriptedModel(draft('The agent reports the gate test passed; original tool evidence is not supplied.',
                                   [visible.prompt]), ACCEPT)
        thought = ThoughtEngine(self.store, model).ingest(visible, [tool, analysis], consolidate=False)[0]
        self.assertEqual(thought.origin['role'], 'assistant')
        for request in model.requests:
            self.assertNotIn('SECRET_', json.dumps(request.records))
            self.assertIn('agent-reported account', request.instructions)
        self.assertEqual(unused.requests, [])

    def test_raw_fallback_cannot_revive_auto_tool_but_manual_tool_provider_remains_allowed(self):
        tool = self.queue.capture_event('fixture', 'tool', 'SECRET_AUTOMATIC_TOOL_RESULT',
            source='codex://fixture', actor='tool', metadata={'role': 'tool'})
        audit = Thought('legacy-audit', 1, 'Old tool summary.', None, {'kind': 'legacy_view'},
                        [{'event_id': tool.id}], {'verification': 'legacy_unverified'}, tool.recorded_at)
        engine = ThoughtEngine(self.store, ScriptedModel())
        # Even an old unclassified summary cannot bypass admission via raw fallback.
        with patch('prototypes.assertion_memory.thought_engine.thought_eligibility', return_value={'allowed': True}):
            with self.assertRaises(SemanticUnavailable):
                engine._current_material('What happened?', [audit])
        manual = self.queue.capture_event('manual', 'tool', 'The manually supplied tool reports a gate fault.',
            source='manual://other-provider', actor='tool', metadata={'role': 'tool'})
        model = ScriptedModel(draft('The supplied tool reports a gate fault.', [manual.prompt]), ACCEPT)
        thought = ThoughtEngine(self.store, model).ingest(manual, consolidate=False)[0]
        self.assertEqual(thought.origin['role'], 'tool')
        self.assertEqual(thought.bases[0]['event_id'], manual.id)


if __name__ == '__main__':
    unittest.main()
