import ast
import asyncio
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prepare import BASELINE, patch as make_patch, sha

FIXTURE = b"""import hashlib
import hmac
import json
import re
from pathlib import Path
BASE = Path('/fictional')
DIGEST = (BASE/'sites-digest').read_text().strip()
if not re.fullmatch(r'[a-f0-9]{64}', DIGEST):
    raise RuntimeError('Missing ingress configuration')
async def app(req):
    if req.method!='POST' or req.url.path!='/memory-graph-read':
        return 404
    auth=req.headers.get('authorization','')
    if not re.fullmatch(r'Bearer [a-f0-9]{64}',auth) or not hmac.compare_digest(hashlib.sha256(auth[7:].encode()).hexdigest(),DIGEST):
        return 401
    return 200
"""

P = '0'*64
T = '1'*64
BAD = '2'*64

def code():
    with patch.dict(BASELINE, production=sha(FIXTURE)):
        result = make_patch(FIXTURE, 'production')
    tree = ast.parse(result)
    chosen = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ('_paired_sites_digest','_sites_authorized','app')]
    return compile(ast.Module(body=chosen,type_ignores=[]),'<reviewed-auth>','exec')


def frame():
    ns=dict(hashlib=hashlib,hmac=hmac,os=os,re=re,stat=stat,DIGEST=sha(P.encode()),PAIRED_DIGEST='')
    exec(code(),ns)
    return ns


class Tests(unittest.TestCase):
    def test_exact_base_only(self):
        raw=FIXTURE
        with self.assertRaises(ValueError): make_patch(raw,'production')
        with patch.dict(BASELINE, production=sha(raw)):
            updated=make_patch(raw,'production')
            with self.assertRaises(ValueError): make_patch(raw,'test')
            with self.assertRaises(ValueError): make_patch(raw+b'\n','production')
        self.assertIn(b"if req.method!='POST' or req.url.path!='/memory-graph-read':",updated)

    def test_guard_preserves_existing_and_accepts_only_exact_second_token(self):
        ns=frame(); auth=ns['_sites_authorized']
        self.assertTrue(auth('Bearer '+P))
        self.assertFalse(auth('Bearer '+T))
        ns['PAIRED_DIGEST']=sha(T.encode())
        self.assertTrue(auth('Bearer '+P))
        self.assertTrue(auth('Bearer '+T))
        for invalid in ('Bearer '+BAD, 'Bearer '+T+'\n', 'Bearer  '+T, 'Bearer '+T+', Bearer '+P, ''):
            self.assertFalse(auth(invalid))

    def test_patched_request_entry_preserves_route_scope(self):
        ns=frame(); ns['PAIRED_DIGEST']=sha(T.encode())
        def request(token, method='POST', route='/memory-graph-read'):
            obj=SimpleNamespace(method=method,url=SimpleNamespace(path=route),headers={'authorization':'Bearer '+token})
            return asyncio.run(ns['app'](obj))
        self.assertEqual(request(P),200)
        self.assertEqual(request(T),200)
        self.assertEqual(request(BAD),401)
        self.assertEqual(request(T,'GET'),404)
        self.assertEqual(request(T,route='/elsewhere'),404)

    def test_optional_sidecar_fails_closed(self):
        ns=frame(); read=ns['_paired_sites_digest']; real_fstat=os.fstat
        with TemporaryDirectory() as tmp:
            path=Path(tmp); f=path/'sites-paired-digest'
            self.assertEqual(read(path),'')
            f.symlink_to(path/'elsewhere')
            self.assertEqual(read(path),'')
            f.unlink()
            os.mkfifo(f)
            self.assertEqual(read(path),'')
            f.unlink()
            f.write_bytes(b'z'*65)
            def own(fd):
                st=real_fstat(fd)
                return SimpleNamespace(st_mode=st.st_mode,st_uid=0)
            with patch.object(os,'fstat',side_effect=own):
                self.assertEqual(read(path),'')
                f.write_bytes(sha(T.encode()).encode()+b'\n')
                self.assertEqual(read(path),sha(T.encode()))
                f.write_bytes(sha(T.encode()).encode()+b'\n\n')
                self.assertEqual(read(path),'')
                f.write_bytes(sha(T.encode()).encode())
                f.chmod(0o660)
                self.assertEqual(read(path),'')
            f.chmod(0o640)
            if os.getuid()!=0:
                self.assertEqual(read(path),'')


if __name__=='__main__': unittest.main()
