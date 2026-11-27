"""Historical package qualification binds copied files; candidate code is only bytes."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from rollback_rebuild_base_input import HELPERS, REQUIRED_BLOBS, TREE_ROOTS, qualify_base_candidate
from rollback_rebuild_origin import Policy


class BaseInputTest(unittest.TestCase):
    def setUp(self):
        self.policy=Policy(21,'a'*40,'assertion-memory-v0.1.25','b'*40,'16','base','base-v1')
        self.app='prototypes/assertion_memory/http_server.py'
        self.index='apps/graph_viewer/web/dist/index.html'
        self.tracked=[self.app,'prototypes/assertion_memory/test_sample.py',
                      'apps/graph_viewer/server/test_fixture.py']
        self.blobs={name: b'blob:'+name.encode() for name in (self.app,*REQUIRED_BLOBS,*HELPERS.values())}
        self.blobs[self.app]=b'raise AssertionError("must not import candidate code")\n'
        self.blobs['apps/graph_viewer/requirements.txt']=b'fastembed==1\nother==2\n'
        self.payload={name:self.blobs[name] for name in (self.app,*REQUIRED_BLOBS)}
        self.payload.update({name:self.blobs[other] for name,other in HELPERS.items()})
        self.payload.update({'REVISION':b'b'*40+b'\n','BASE_RELEASE_TAG':self.policy.tag.encode()+b'\n',
                             'QA_PROFILE':b'assertion-memory-synthetic-qa-v1\n',
                             'mcp-tool-schema.json':b'{}\n','requirements-graph-lexical.txt':b'other==2\n',
                             self.index:b'<main>test</main>'})

    def gate(self,replacement=None):
        members=self.payload.copy()
        for key,value in (replacement or {}).items():
            if value is None:del members[key]
            else:members[key]=value
        members['SHA256SUMS']=b''.join(hashlib.sha256(value).hexdigest().encode()+b'  '+name.encode()+b'\n'
                                       for name,value in sorted(members.items()))
        def ls(commit,roots):
            self.assertEqual((commit,roots),(self.policy.revision,TREE_ROOTS))
            return self.tracked
        def read(commit,name):
            self.assertEqual(commit,self.policy.revision)
            return self.blobs[name]
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'candidate.tar.gz'
            with tarfile.open(archive,'w:gz') as t:
                for name,value in sorted(members.items()):
                    entry=tarfile.TarInfo(name)
                    entry.size=len(value)
                    t.addfile(entry,io.BytesIO(value))
            digest=hashlib.sha256(archive.read_bytes()).hexdigest()
            return qualify_base_candidate(archive,self.policy,digest,ls,read),digest

    def test_members_remain_exact_and_code_is_not_executed(self):
        key,digest=self.gate()
        self.assertEqual(key,self.policy.revision+'-'+digest)

    def test_rejects_self_consistent_but_wrong_candidate_bytes(self):
        for key,value in ((self.app,b'replaced'),('BASE_RELEASE_TAG',b'assertion-memory-v0.1.24\n'),
                          ('REVISION',b'a'*40+b'\n'),('requirements-graph-lexical.txt',b'fastembed==1\n'),
                          ('smoke_local.py',b'replaced'),('unapproved.py',b'new'),
                          (self.index,None),('apps/graph_viewer/web/dist/app.js.map',b'unapproved')):
            with self.subTest(key=key),self.assertRaises(ValueError):self.gate({key:value})

    def test_refuses_provider_path_outside_known_roots(self):
        self.tracked.append('tools/extra.py')
        with self.assertRaises(ValueError):self.gate()


if __name__=='__main__':
    unittest.main()
