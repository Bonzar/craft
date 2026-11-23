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
        self.assertEqual([action for action,_ in self.calls],['prepare','restart','smoke']) # staging alone is not TEST preparation
        self.assertEqual((self.root/'current').resolve(),self.root/'releases'/f'{REV}-{sha}')

    def test_process_dies_during_prepare_then_same_archive_restarts_cleanly(self):
        old,_=self.execute()
        current=self.root/'current'
        previous=current.resolve()
        sha,body=archive([('./notice.txt',b'new')])
        raw=body.getvalue()
        release=self.root/'releases'/f'{REV}-{sha}'
        def die_during_prepare(_script, action, path):
            if action != 'prepare':
                os._exit(98)
            (path/'half-venv').touch()
            os._exit(67)
        pid=os.fork()
        if pid==0:
            try:
                deploy('test',REV,sha,io.BytesIO(raw),root=self.root,config=self.config,hook=die_during_prepare)
            finally:
                os._exit(99)
        _,status=os.waitpid(pid,0)
        self.assertEqual(os.waitstatus_to_exitcode(status),67)
        self.assertTrue((release/'half-venv').is_file())
        self.assertEqual(current.resolve(),previous)
        self.calls=[]
        def retry_hook(script, action, path):
            self.assertFalse((path/'half-venv').exists())
            if action=='prepare':
                self.assertEqual(current.resolve(),previous)
            self.hook(script,action,path)
        self.assertEqual(deploy('test',REV,sha,io.BytesIO(raw),
                               root=self.root,config=self.config,hook=retry_hook),'ok')
        self.assertEqual([action for action,_ in self.calls],['prepare','restart','smoke'])
        self.assertEqual(current.resolve(),release)
        self.assertTrue(previous.is_dir())

    def test_crash_after_swap_then_failed_candidate_smoke_recovers_previous(self):
        a_sha,_=self.execute()
        previous=self.root/'releases'/f'{REV}-{a_sha}'
        b_sha,data=archive([('./notice.txt',b'B')])
        blob=data.getvalue()
        candidate=self.root/'releases'/f'{REV}-{b_sha}'
        current=self.root/'current'
        def run(body,hook):
            return deploy('test',REV,b_sha,io.BytesIO(body),root=self.root,config=self.config,hook=hook)
        def crash(_,action,path):
            if action=='smoke' and path==candidate:
                os._exit(67 if current.resolve()==candidate else 98)
        pid=os.fork()
        if pid==0:
            try:
                run(blob,crash)
            finally:
                os._exit(99)
        _,status=os.waitpid(pid,0)
        self.assertEqual(os.waitstatus_to_exitcode(status),67)
        self.assertEqual(current.resolve(),candidate)
        self.calls=[]
        def fail_b(_,action,path):
            self.calls.append((action,path))
            if action=='smoke' and path==candidate:
                raise RuntimeError('smoke')
        with self.assertRaisesRegex(RuntimeError,'smoke'):
            run(blob,fail_b)
        self.assertEqual(current.resolve(),previous)
        self.assertEqual(self.calls,[('restore',previous),('smoke',previous),
                         ('restart',candidate),('smoke',candidate),
                         ('restore',previous),('smoke',previous)])

    def test_unmarked_current_and_symlink_are_preserved(self):
        sha,body=archive()
        raw=body.getvalue()
        release=self.root/'releases'/f'{REV}-{sha}'
        release.mkdir()
        old_data=release/'legacy'
        old_data.write_text('keep')
        current=self.root/'current'
        current.symlink_to(release)
        with self.assertRaisesRegex(ValueError,'unverified_current_release'):
            deploy('test',REV,sha,io.BytesIO(raw),root=self.root,config=self.config,hook=self.hook)
        self.assertEqual(current.resolve(),release)
        self.assertEqual(old_data.read_text(),'keep')
        other=self.root/'releases'/'other'
        release.rename(other)
        release.symlink_to(other)
        with self.assertRaisesRegex(ValueError,'invalid_staged_release'):
            deploy('test',REV,sha,io.BytesIO(raw),root=self.root,config=self.config,hook=self.hook)
        self.assertEqual((other/'legacy').read_text(),'keep')
        self.assertFalse(self.calls)

    def test_completed_prepare_not_repeated_for_same_exact_archive(self):
        sha,body=archive()
        raw=body.getvalue()
        for _ in range(2):
            self.assertEqual(deploy('test',REV,sha,io.BytesIO(raw),
                                   root=self.root,config=self.config,hook=self.hook),'ok')
        self.assertEqual([action for action,_ in self.calls],['prepare','restart','smoke','restart','smoke'])

    def test_provisioner_embeds_exact_reviewed_runtime_files(self):
        here=Path(__file__).resolve().parent
        script=(here/'provision-test.sh').read_bytes()
        for label,filename in [(b'RECEIVER_PY','receive.py'),(b'HOOK_SH','test-service-hook.sh')]:
            embedded=script.split(b"<<'"+label+b"'\n",1)[1].split(b'\n'+label+b'\n',1)[0]+b'\n'
            self.assertEqual(embedded,(here/filename).read_bytes(),filename)

    def test_distinct_prep_and_smoke_failure_rollback(self):
        old,_=self.execute()
        self.assertEqual([action for action,_ in self.calls],['prepare','restart','smoke'])
        self.calls=[]
        previous=self.root/'releases'/f'{REV}-{old}'
        def fail_new(script,action,path):
            self.hook(script,action,path)
            if action=='smoke' and path!=previous:
                raise RuntimeError('smoke')
        sha,body=archive([('./notice.txt',b'new')])
        with self.assertRaisesRegex(RuntimeError,'smoke'):
            deploy('test',REV,sha,body,root=self.root,config=self.config,hook=fail_new)
        self.assertEqual([action for action,_ in self.calls],['prepare','restart','smoke','restore','smoke'])
        self.assertEqual((self.root/'current').resolve(),previous)
        self.assertFalse((self.root/'.activation-pending-v1').exists())

    def test_rollback_smoke_failure_keeps_journal_blocks_new_attempts(self):
        old,_=self.execute()
        previous=self.root/'releases'/f'{REV}-{old}'
        current=self.root/'current'
        journal=self.root/'.activation-pending-v1'
        self.failure='smoke'
        with self.assertRaisesRegex(RuntimeError,'smoke'):
            self.execute(extra=[('./notice.txt',b'B')])
        pending=journal.read_bytes()
        self.assertEqual(current.resolve(),previous)
        sha,body=archive([('./notice.txt',b'C')])
        for _ in range(2):
            body.seek(0)
            self.calls=[]
            with self.assertRaisesRegex(RuntimeError,'smoke'):
                deploy('test',REV,sha,body,root=self.root,config=self.config,hook=self.hook)
            self.assertEqual(body.tell(),0)
            self.assertEqual(self.calls,[('restore',previous),('smoke',previous)])
            self.assertEqual(journal.read_bytes(),pending)
            self.assertEqual(current.resolve(),previous)
        self.failure=None
        self.assertEqual(deploy('test',REV,sha,body,root=self.root,config=self.config,hook=self.hook),'ok')
        self.assertFalse(journal.exists())

    def test_corrupt_pending_journal_never_deploys(self):
        old,_=self.execute()
        previous=self.root/'releases'/f'{REV}-{old}'
        journal=self.root/'.activation-pending-v1'
        journal.write_text('{"old":"../../outside","new":"unexpected"}')
        self.calls=[]
        with self.assertRaisesRegex(ValueError,'invalid_activation_journal'):
            self.execute(extra=[('./notice.txt',b'B')])
        self.assertFalse(self.calls)
        self.assertTrue(journal.exists())
        self.assertEqual((self.root/'current').resolve(),previous)

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
