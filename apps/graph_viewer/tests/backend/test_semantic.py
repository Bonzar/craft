"""Semantic search uses vectors from the matching source model, never keywords."""
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_service import fixture
from apps.graph_viewer.server.projection import prepare
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource
from apps.graph_viewer.server.search import search


class VectorBoundary:
    identity='fastembed:controlled-test-model'
    def __init__(self): self.calls=[]
    def __call__(self,texts):
        self.calls.extend(texts)
        return [[0.,1.] for _ in texts]


class SemanticContract(unittest.TestCase):
    def test_matching_source_vectors_reused_query_warmup_and_snapshot_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3'; fixture(path)
            import hashlib
            with sqlite3.connect(path) as c:
                c.execute('CREATE TABLE memory_embedding_cache(backend TEXT,text_digest TEXT,vector BLOB,PRIMARY KEY(backend,text_digest))')
                for id,text in c.execute('SELECT assertion_id,text FROM memory_assertions').fetchall():
                    vector=(0.,1.) if id=='unicode' else (1.,0.)
                    c.execute('INSERT INTO memory_embedding_cache VALUES(?,?,?)',('fastembed:controlled-test-model',hashlib.sha256(text.encode()).hexdigest(),struct.pack('<ff',*vector)))
            source=AssertionMemorySource(path)
            graph=prepare(source,Path(tmp)/'cache',{'mode':'current','at':None,'history':True,'undated':'separate'})
            model=VectorBoundary()
            # Use provider boundary injection; public semantic search remains a real dot product.
            from apps.graph_viewer.server import semantic
            semantic.prepare_index(source,graph,model)
            self.assertEqual(model.calls,['query warmup'])
            result=search(graph,{'mode':'semantic','q':'unrelated words absent from text'})
            self.assertEqual(result['hits'][0]['id'],'unicode')
            self.assertIn('unrelated words absent from text',model.calls)
            source.close()

if __name__=='__main__':unittest.main()

class PreparationSeparation(unittest.IsolatedAsyncioTestCase):
    async def test_full_graph_is_readable_while_semantic_index_is_preparing(self):
        import asyncio
        import threading
        from apps.graph_viewer.server.manager import GraphManager
        class SlowModel(VectorBoundary):
            gate=threading.Event()
            def __call__(self,texts):
                self.gate.wait(timeout=2)
                return super().__call__(texts)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3';fixture(path)
            model=SlowModel();manager=GraphManager(AssertionMemorySource(path),Path(tmp)/'cache',model)
            selection={'mode':'current','at':None,'history':False,'undated':'separate'}
            try:
                for _ in range(20):
                    graph=await manager.ensure(selection)
                    if graph:break
                    await asyncio.sleep(.02)
                self.assertIsNotNone(graph,'Keyword/card/full-graph readiness must not wait for semantic vector computation')
                self.assertEqual(graph.manifest['counts']['nodes'],6)
            finally:
                model.gate.set()
                await manager.stop()
