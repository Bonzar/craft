import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'modules'/'assertion-memory'))
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from lib.native_capture import register
from lib.outbox import Outbox
from lib.private_http import MachineClient
from lib.remote_client import TransportError
import io,json,unittest

class Verified(unittest.TestCase):
    def test_fresh_entry(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'test.jsonl'
            clock='2000-01-01T00:00:00Z'
            bind={'session_id':'fictional','cwd':temp,'transcript_path':str(path),'source':'startup'}
            db=Outbox(Path(temp)/'outbox.sqlite3')
            def header(stamp='2025-01-01T00:00:00Z',**extra):
                path.write_text(json.dumps({'timestamp':stamp,'type':'session_meta','payload':{'id':'fictional','cwd':temp,**extra}})+'\n')
            for source in ('resume','fork'):
                with self.assertRaises(ValueError): register(db,{**bind,'source':source},fresh_since=clock)
            with self.assertRaises(ValueError):register(db,bind,fresh_since=clock)
            for extra in ('parent_session_id','forked_from_id','resumed_from'):
                header(**{extra:'previous'})
                with self.assertRaises(ValueError): register(db,bind,fresh_since=clock)
            header(stamp='1999-01-01T00:00:00Z')
            with self.assertRaises(ValueError):register(db,bind,fresh_since=clock)
            self.assertEqual(db.sessions(),[])
            header()
            self.assertEqual(register(db,bind,fresh_since=clock)['cursor'],0)
            with self.assertRaises(ValueError):register(db,bind,fresh_since=clock)
            db.close()
    def test_module_context_starts_worker_only_for_fresh_startup(self):
        import hashlib
        from unittest.mock import patch
        from .test_assertion_memory_capture import load_capture
        from jarvis.event import Event
        from jarvis.storage import Storage
        load_capture()
        import synthetic_memory_adapter as library
        with TemporaryDirectory() as temp:
            root=Path(temp)
            cutover='2000-01-01T00:00:00Z'
            url='https://example.test/memory-api-prod'
            key=hashlib.sha256(('private_base_http\x00prod\x00'+url+'\x00'+cutover).encode()).hexdigest()
            cfg={'harness':'codex','server_name':'assertion_memory','endpoint_sha256':key,
                 'connection':{'transport':'private_base_http','url':url},
                 'capture':{'enabled':True,'fresh_since':cutover,'state_dir':temp}}
            binding=root/'module'/'mcp'/'installed.json'
            binding.parent.mkdir(parents=True);binding.write_text(json.dumps(cfg))
            path=root/'fictional.jsonl'
            path.write_text(json.dumps({'type':'session_meta','timestamp':'2025-01-01T00:00:00Z',
                 'payload':{'id':'fixture','cwd':temp}})+'\n')
            raw={'session_id':'fixture','cwd':temp,'transcript_path':str(path)}
            store=Storage(root/'hook-state','fixture')
            def hook(source):return library.context(Event('session-start','fixture',temp,'codex',raw={**raw,'source':source}),store)
            with patch.object(library,'__file__',str(binding.parent.parent/'lib'/'__init__.py')), patch.object(library.subprocess,'Popen') as spawn:
                hook('resume')
                self.assertFalse((root/key).exists())
                spawn.assert_not_called()
                hook('startup')
                spawn.assert_called_once()
                self.assertIn(str(root/key),spawn.call_args.args[0])
                hook('startup')
                spawn.assert_called_once()
    def test_transport(self):
        calls=[]
        def run(req,timeout):
            call=json.loads(req.data)
            calls.append(call)
            info={'environment':'prod','request_id':call['request_id'],'generation':'b'*64,'catalog_sha256':'c'*64,'release':'assertion-memory-v0.1.3'}
            if call['operation']=='catalog':
                info.update(capability='machine',tools=[{'name':'memory_capture_message'}])
            else:
                self.assertEqual(call['expected'],{k:info[k] for k in call['expected']})
                info.update(status='ok',result={'accepted':True})
            return io.BytesIO(json.dumps(info).encode())
        c=MachineClient({'url':'https://example.test/memory-api-prod'},opener=SimpleNamespace(open=run),credential=lambda:'a'*64)
        self.assertEqual(c.call('memory_capture_message',{'synthetic':True}),{'accepted':True})
        with self.assertRaises(TransportError):c.call('memory_process',{})
        self.assertEqual(len(calls),2)
if __name__=='__main__':unittest.main()
