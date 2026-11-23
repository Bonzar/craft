import base64
import tempfile
from pathlib import Path
import unittest

from test_producer import EXACT, PROD_KEY, WORKFLOW, exact_caller, verify

class Producer(unittest.TestCase):
    def test_exact_allowed_block_cannot_run_other_source(self):
        reusable='e'*40
        sha='a'*40
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            prelude=b'name: fixed\njobs:\n  deploy-test:\n    name: exact'
            trusted={WORKFLOW:prelude+PROD_KEY+b'    name: draft\n',**{f:('reviewed '+f).encode() for f in EXACT}}
            for f,value in trusted.items():
                dst=p/f
                dst.parent.mkdir(parents=True,exist_ok=True)
                dst.write_bytes(value)
            actual={**trusted,WORKFLOW:prelude+PROD_KEY+exact_caller(reusable)}
            def get(route):
                self.assertTrue(route.endswith('?ref='+sha))
                path=route.removeprefix('/repos/Bonzar/craft/contents/').split('?')[0]
                return dict(path=path,type='file',encoding='base64',content=base64.b64encode(actual[path]).decode())
            verify(get,'/repos/Bonzar/craft',sha,reusable,p)
            for guard in (b"&& github.ref_protected", b"&& vars.ASSERTION_MEMORY_PROD_BRIDGE_CALLER_READY == 'true'"):
                saved=actual[WORKFLOW]
                self.assertIn(guard,saved)
                actual[WORKFLOW]=saved.replace(guard,b'')
                with self.assertRaisesRegex(ValueError,'audited'):
                    verify(get,'/repos/Bonzar/craft',sha,reusable,p)
                actual[WORKFLOW]=saved
            for name in (WORKFLOW,*EXACT):
                saved=actual[name]; actual[name]+=b'\n# modified'
                with self.assertRaisesRegex(ValueError,'audited'):
                    verify(get,'/repos/Bonzar/craft',sha,reusable,p)
                actual[name]=saved
