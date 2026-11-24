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
            def header(stamp='2026-11-24T00:00:00Z',**extra):
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
