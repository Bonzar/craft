from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from apps.graph_viewer.server.common import APIError
from apps.graph_viewer.server.node_removal_read_preview import preview_available, removal_preview
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_runtime import initialize_empty
from prototypes.assertion_memory.thought_store import ThoughtStore


class ReadOnlyNodePreview(TestCase):
    def fixture(self,root,stage='test',signed_in=True):
        data=Path(root)/'fictional.sqlite3'
        with MemoryStore(data) as storage:
            initialize_empty(storage)
            thought=ThoughtStore(storage).publish('Fictional preview only')
        dashboard=SimpleNamespace(stage=stage,db=data,release_lease=nullcontext,activation=lambda:('r','a'))
        state=SimpleNamespace(queue_dashboard=dashboard,browser_owner_api=SimpleNamespace(stage=stage),
            owner=SimpleNamespace(session=lambda _: 'fixture' if signed_in else None),
            manager=SimpleNamespace(source=SimpleNamespace(source_path=data)))
        request=SimpleNamespace(method='GET',app=SimpleNamespace(state=state))
        return data,request,dict(is_current=True,thought_ref={'thought_id':thought.id,'version':1})

    def test_uses_real_canonical_preview_without_any_database_effect(self):
        with TemporaryDirectory() as d:
            db,request,card=self.fixture(d)
            self.assertTrue(preview_available(request))
            before=sha256(db.read_bytes()).digest()
            import sqlite3
            import apps.graph_viewer.server.node_removal_read_preview as mod
            connect=sqlite3.connect
            def readonly(uri_text,*a,**kw):
                self.assertTrue(uri_text.endswith('?mode=ro'))
                return connect(uri_text,*a,**kw)
            with patch.object(mod.sqlite3,'connect',side_effect=readonly):
                self.assertEqual(removal_preview(request,card),dict(eligible=True,reason=None,write_authority=False))
                card['thought_ref']['version']=2
                missing=removal_preview(request,card)
                self.assertFalse(missing['eligible'])
                self.assertFalse(missing['write_authority'])
            self.assertEqual(before,sha256(db.read_bytes()).digest())

    def test_prod_anonymous_and_non_get_are_denied_before_open(self):
        for lane,signed,method in (('prod',True,'GET'),('test',False,'GET'),('test',True,'POST')):
            with TemporaryDirectory() as d:
                _,request,card=self.fixture(d,lane,signed)
                request.method=method
                self.assertFalse(preview_available(request))
                with patch('apps.graph_viewer.server.node_removal_read_preview.exact_database',
                           side_effect=AssertionError('must not open database')):
                    with self.assertRaises(APIError) as result:
                        removal_preview(request,card)
                    self.assertEqual(result.exception.status,404)

    def test_foreign_database_does_not_advertise_preview(self):
        with TemporaryDirectory() as d:
            _,request,card=self.fixture(d)
            request.app.state.manager.source.source_path=Path(d)/'other.sqlite3'
            self.assertFalse(preview_available(request))
            with self.assertRaises(APIError) as result:
                removal_preview(request,card)
            self.assertEqual(result.exception.status,404)
