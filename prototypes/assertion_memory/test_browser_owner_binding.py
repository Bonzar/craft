from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from .browser_owner_binding import base_owner_browser
from prototypes.assertion_memory.storage import MemoryStore

class OwnerTest(TestCase):
    def test_real_owner_domain_single_lease_pause_and_clear_disabled(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/'fictional.sqlite3'
            with MemoryStore(str(db)):
                pass
            state=SimpleNamespace(db=db, provider='none',
                auto_process_fresh_mac=False,processing_session_ids=())
            leases=[]
            @contextmanager
            def lease():
                self.assertFalse(leases)
                leases.append(True)
                try:
                    yield
                finally:
                    self.assertTrue(leases.pop())
            identity=lambda:('assertion-memory-v0.1.34','a'*64)
            api=base_owner_browser(stage='prod',config=state,verified_identity=identity,
                                   release_lease=lease,interactive_enabled=False)
            receipt=api.perform('pause_prepare',{})
            status=api.perform('pause_set',{'paused':True,'expected_revision':receipt['revision']})
            self.assertTrue(status['paused'])
            self.assertFalse(leases)
            self.assertTrue(api.queue_snapshot(limit=1,offset=0)['processing_control']['paused'])
            self.assertFalse(any('clear' in route.path for route in api.routes()))
            with self.assertRaises(ValueError):
                api.perform('clear_commit',{})
