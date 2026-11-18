"""A changed search universe must not reuse an old global coverage verdict."""
import unittest

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelRequest, format_prompt
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


class GenerationCacheContextTest(unittest.TestCase):
    def test_cache_context_is_not_part_of_model_wire_input(self):
        first = ModelRequest("Review coverage", [{"id": "known", "text": "Known source."}],
                             cache_context="corpus-one")
        second = ModelRequest("Review coverage", [{"id": "known", "text": "Known source."}],
                              cache_context="corpus-two")
        self.assertEqual(format_prompt(first), format_prompt(second))
        self.assertNotIn("corpus-one", format_prompt(first))
        self.assertNotIn("cache_context", format_prompt(second))
        self.assertEqual(first, second)

    def test_real_worker_revalidates_new_universe_and_reuses_the_same_universe(self):
        with MemoryStore(":memory:") as store:
            queue = CaptureQueue(store)
            calls, observed = [], []
            current = ["corpus-one"]
            def provider(request):
                calls.append(request)
                return f"independent-review-{len(calls)}"
            engine = MemoryEngine(store, model=provider)
            # The worker controls durable generation caching around a view
            # consumer. Identical wire input intentionally hides corpus identity.
            def refresh(assertion_id):
                request = ModelRequest("Review coverage", [{"id": "known", "text": "Known source."}],
                                       cache_context=current[0])
                observed.append(engine.model(request))
                return []
            engine.refresh_from = refresh
            worker = LinkWorker(queue, engine, lambda request: '{"links":[]}', source_first=True)
            for key, revision in (("first", "corpus-one"), ("second", "corpus-two"),
                                  ("third", "corpus-two")):
                current[0] = revision
                queue.capture(CaptureInput("Fixture source " + key, "fixture", "fixture://" + key,
                                           "fixture", assertion_id=key))
                self.assertEqual(worker.run_once().phase, "refresh")
                self.assertEqual(worker.run_once().status, "completed")
            self.assertEqual(observed, ["independent-review-1", "independent-review-2", "independent-review-2"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(format_prompt(calls[0]), format_prompt(calls[1]))
            self.assertEqual([r.cache_context for r in calls], ["corpus-one", "corpus-two"])


if __name__ == "__main__":
    unittest.main()
