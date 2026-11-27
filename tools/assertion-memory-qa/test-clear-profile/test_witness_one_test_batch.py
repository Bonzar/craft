import asyncio
import fcntl
import io
import os
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from compile_functional_release import compile_release
from vm_relay import make_app
from prototypes.assertion_memory.http_server import create_http_app
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.operation_registry import create_operations
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.release_catalog import export
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_runtime import initialize_empty
import install_profile
import witness_one_test_batch as w

class ProtectedLoopbackWitness(unittest.IsolatedAsyncioTestCase):
    async def test_live_machine_contract_one_fictional_post_clear_batch(self):
        release,generation='assertion-memory-v0.1.36','a'*64
        machine,sites,legacy='c'*64,'b'*64,'e'*64
        canonical=await export('d'*40)
        pins={k:canonical[k] for k in ('revision','module_sha256','schema_sha256')}
        pins['registry']='base-typed-operations-v1'
        catalog=compile_release(canonical,pins,effects=False)
        profile={'lane':'test','base_release':release,'enabled_capabilities':['baseline_read'],
                 'catalog_sha256':catalog['catalog_sha256'],'require_catalog_identity':True}
        with TemporaryDirectory() as tmp:
            db=Path(tmp).resolve()/'fictional.sqlite3'
            with MemoryStore(db) as store:
                self.assertTrue(initialize_empty(store))
                MemoryService(store).capture_message('previous-session','old1','some-fixture','user','Old fictional text',1,'fixture')
            with patch.object(install_profile,'TEST_DB',str(db)):
                prior=install_profile.scope()
                owner=owner_operations(database=db,release_lease=nullcontext,identity=lambda:(release,generation),stage='test')
                first=owner('clear_prepare',{})
                self.assertEqual(first['scope'],prior)
                self.assertTrue(owner('clear_commit',{'expected_revision':first['revision']})['cleared'])
                current=install_profile.scope()
                self.assertNotEqual(prior,current)
                def neutral(config,store,service,background):
                    ops=create_operations(config,store,background=background,service=service)
                    return frozenset((w.PATH,'/memory-mcp-test-tools')),make_app(profile,catalog,sites,lambda:(release,generation),
                        operations=ops,machine_digests=(sha256(machine.encode('ascii')).hexdigest(),))
                app=create_http_app(ServiceConfig(db=str(db),provider='none',processing_session_ids=[]),
                    legacy,'memory.example',background=False,neutral_factory=neutral)
                async with app.lifespan(), AsyncClient(transport=ASGITransport(app=app),base_url='https://memory.example') as cli:
                    loop=asyncio.get_running_loop()
                    sent=[]
                    def post(body):
                        sent.append(body['operation'])
                        r=asyncio.run_coroutine_threadsafe(cli.post(w.PATH,
                            headers={'Authorization':'Bearer '+machine},json=body),loop).result(timeout=5)
                        self.assertEqual(r.status_code,200,r.content[:250])
                        return r.json()
                    proof=await asyncio.to_thread(w.discover,post,release=release,generation=generation)
                    self.assertEqual(sent,['catalog'])
                    args=dict(proof=proof,previous_scope=prior,reset_scope=current,
                              witness_id='11111111-2222-3333-4444-555555555555')
                    with self.assertRaises(ValueError):
                        await asyncio.to_thread(w.witness,post,**args|{'reset_scope':'e'*64})
                    self.assertEqual(sent,['catalog'])
                    result=await asyncio.to_thread(w.witness,post,**args)
                    self.assertEqual(result['member_count'],2)
                    self.assertTrue(result['stream_closed'])
                    self.assertEqual(sent.count('memory_capture_batch'),1)
                with sqlite3_read(db) as c:
                    self.assertEqual(c.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],2)
                    self.assertEqual(c.execute('SELECT count(*) FROM memory_capture_batches').fetchone()[0],1)
                    self.assertEqual(c.execute('SELECT count(*) FROM memory_capture_batch_members').fetchone()[0],2)


    def test_cli_default_probes_the_pinned_string_release_only(self):
        output=io.StringIO()
        def post(envelope):
            self.assertEqual(envelope['operation'],'catalog')
            return {'request_id':envelope['request_id'],'environment':'test','capability':'machine',
                    'release':w.TAG,'generation':w.GENERATION,'catalog_sha256':'a'*64,
                    'tools':[{'name':x} for x in w.TOOLS]}
        with patch.object(sys,'argv',['witness_one_test_batch.py']),patch.object(w,'installed_client',return_value='b'*64), \
             patch.object(w,'loopback',return_value=post),patch.object(w,'witness') as effect,redirect_stdout(output):
            w.main()
            effect.assert_not_called()
        self.assertIn('"approved_existing_batch_catalog": true',output.getvalue())
        self.assertNotIn('b'*64,output.getvalue())

    def test_existing_epoch_lease_never_creates_and_blocks_a_second_clear(self):
        with TemporaryDirectory() as tmp:
            exact=Path(tmp).resolve()/'fictional.sqlite3.clear.lock'
            with patch.object(w,'CLEAR_LOCK',exact), patch.object(w.pwd,'getpwnam',
                                                    return_value=SimpleNamespace(pw_uid=os.getuid())):
                with self.assertRaises(FileNotFoundError):
                    with w.existing_clear_lease():pass
                fd=os.open(exact,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                try:
                    with w.existing_clear_lease():
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally:os.close(fd)

def sqlite3_read(path):
    import sqlite3
    from contextlib import closing
    return closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True))

if __name__=='__main__':unittest.main()
