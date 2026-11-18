"""Optional adapters and the standalone tool share one semantic cache."""
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.runtime_config import build_candidate_search
from prototypes.assertion_memory.storage import MemoryStore


class SharedEmbeddingCacheTest(unittest.TestCase):
    def test_standalone_embeddings_are_immediately_available_to_optional_adapter(self):
        calls = []
        def backend(texts):
            calls.append(list(texts))
            return [[float(len(text)), 1.0] for text in texts]
        with MemoryStore(':memory:') as store:
            config = ServiceConfig('/private/tmp/cache-fixture.sqlite3', 'none',
                                   embedding_mode='fastembed', embedding_model='fixture-model',
                                   embedding_cache='/private/tmp/cache-fixture')
            with patch('prototypes.assertion_memory.mcp_server.FastEmbedModel', return_value=backend), \
                 patch('prototypes.assertion_memory.runtime_config.FastEmbedModel', return_value=backend):
                standalone = config.engine(store).candidates
                standalone.embedder(['shared statement'])
                adapter = build_candidate_search(store, environ={
                    'ASSERTION_MEMORY_EMBEDDING_MODE':'fastembed',
                    'ASSERTION_MEMORY_EMBEDDING_MODEL':'fixture-model',
                    'ASSERTION_MEMORY_EMBEDDING_CACHE':'/private/tmp/cache-fixture'})
                self.assertTrue(adapter.embedder.covers(['shared statement']))
                adapter.embedder(['shared statement'])
        self.assertEqual(calls, [['shared statement']])


if __name__ == '__main__':
    unittest.main()
