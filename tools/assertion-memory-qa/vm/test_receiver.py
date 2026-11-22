import hashlib, io, os, tarfile, tempfile, unittest
from pathlib import Path
from receive import deploy, stage, COMMAND

REV='a'*40

def archive(extra=()):
    out=io.BytesIO()
    paths={'./REVISION':REV.encode()+b'\n','./QA_PROFILE':b'assertion-memory-synthetic-qa-v1\n',
           './prototypes/assertion_memory/http_server.py':b'ok',
           './apps/graph_viewer/web/dist/index.html':b'ok'}
    with tarfile.open(fileobj=out,mode='w:gz') as tar:
        root=tarfile.TarInfo('.');root.type=tarfile.DIRTYPE;tar.addfile(root)
        members=[*paths.items(),*extra]
        manifest=''.join(f'{hashlib.sha256(val).hexdigest()}  {name}\n' for name,val in members).encode()
        for name,val in [*members,('./SHA256SUMS',manifest)]:
            member=tarfile.TarInfo(name);member.size=len(val);tar.addfile(member,io.BytesIO(val))
    raw=out.getvalue()
    return hashlib.sha256(raw).hexdigest(),io.BytesIO(raw)

class Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'isolated';self.root.mkdir()
        self.config=Path(self.tmp.name)/'config';self.config.mkdir()
        (self.root/'releases').mkdir()
        (self.config/'service-hook.sh').touch()
        self.calls=[]
        self.failure=None

    def hook(self,script,action,path):
        self.calls.append((action,path))
        if action==self.failure:
            raise RuntimeError(action)

    def execute(self,env='test',extra=()):
        sha,body=archive(extra)
        return sha,deploy(env,REV,sha,body,root=self.root,config=self.config,hook=self.hook)

    def test_test_smoke_and_production_gate(self):
        sha,status=self.execute(env='production')
        self.assertEqual(status,'staged')
        self.assertFalse((self.root/'current').exists())
        self.assertFalse(self.calls)
        sha,status=self.execute()
        self.assertEqual(status,'ok')
        self.assertEqual([action for action,_ in self.calls],['restart','smoke']) # already staged in test harness shared mock root
        self.assertEqual((self.root/'current').resolve(),self.root/'releases'/f'{REV}-{sha}')

    def test_distinct_prep_and_smoke_failure_rollback(self):
        old,_=self.execute()
        self.assertEqual([action for action,_ in self.calls],['prepare','restart','smoke'])
        self.calls=[]
        self.failure='smoke'
        with self.assertRaisesRegex(RuntimeError,'smoke'):
            self.execute(extra=[('./notice.txt',b'new')])
        self.assertEqual([action for action,_ in self.calls],['prepare','restart','smoke','restore'])
        self.assertEqual((self.root/'current').resolve(),self.root/'releases'/f'{REV}-{old}')

    def test_failed_first_release_leaves_no_current(self):
        self.failure='smoke'
        with self.assertRaisesRegex(RuntimeError,'smoke'):
            self.execute()
        self.assertFalse((self.root/'current').exists())
        self.assertEqual(self.calls[-1][0],'stop')

    def test_bad_archive_and_scope(self):
        for path in ('../outside','/absolute','./state.sqlite','./.ssh/authorized_keys'):
            with self.assertRaises(ValueError):
                self.execute(extra=[(path,b'disallowed')])
        self.assertFalse(self.calls)
        self.assertFalse((self.root/'current').exists())
        match=COMMAND.fullmatch(f'deploy-release production {REV} '+ 'c'*64)
        self.assertNotEqual(match.group(1),'test')
        self.assertIsNone(COMMAND.fullmatch(f'deploy-release test {REV} '+ 'c'*64+'; sh'))

if __name__=='__main__':unittest.main()
