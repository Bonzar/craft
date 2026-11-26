from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from sites_graph_release_gate import (SitesGraphReleaseGate, ProductionGraphIdentity, identity,
                                     drop_in, matches_status, UNIT, ACTOR, WRAPPER)


class SitesGraphGateTest(unittest.TestCase):
    def prepare_release(self,root,letter,tag):
        p=root/'releases'/(letter*40+'-'+'b'*64)
        p.mkdir(parents=True)
        files={'REVISION':letter*40+'\n','BASE_RELEASE_TAG':tag+'\n',
            'apps/graph_viewer/server/app.py':'raise Exception("never import")',
            'apps/graph_viewer/server/queue_dashboard.py':'raise Exception("never import")',
            'prototypes/assertion_memory/dashboard_projection.py':'raise Exception("never import")'}
        lines=[]
        for name,txt in files.items():
            f=p/name
            f.parent.mkdir(exist_ok=True,parents=True)
            f.write_text(txt)
            lines.append(sha256(txt.encode()).hexdigest()+'  ./'+name)
        (p/'SHA256SUMS').write_text('\n'.join(lines)+'\n')
        return p

    def test_preflight_binds_release_inodes_and_preserves_old_control_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            db=root/'canonical.sqlite3'; db.touch()
            old=self.prepare_release(root,'a','assertion-memory-v1.2.3')
            new=self.prepare_release(root,'c','assertion-memory-v1.2.4')
            (root/'current').symlink_to(old)
            a=identity(old,root=root,db=db,require_current=True)
            b=identity(new,root=root,db=db)
            self.assertEqual((a.db_device,a.db_inode),(b.db_device,b.db_inode))
            self.assertNotEqual(a.generation,b.generation)
            with self.assertRaises(ValueError):
                identity(new,root=root,db=db,require_current=True)
            (new/'apps/graph_viewer/server/app.py').write_text('tampered')
            with self.assertRaises(ValueError):
                identity(new,root=root,db=db)
        cfg=drop_in()
        self.assertNotIn('ExecStart',cfg)
        self.assertNotIn('User=',cfg)
        self.assertNotIn('ExecCondition',cfg)
        self.assertIn('ASSERTION_MEMORY_QUEUE_DASHBOARD_STAGE=prod',cfg)

    def test_graph_restart_only_after_authoritative_settlement_and_exact_gateway(self):
        temp=Path('/server/immutable/test-only-fixture')
        x=ProductionGraphIdentity('assertion-memory-v1.2.4','d'*64,temp/'new',1,2)
        y=ProductionGraphIdentity('assertion-memory-v1.2.3','f'*64,temp/'old',1,2)
        state=SimpleNamespace(current=y,settled=False,seen=[],reply=y)
        @contextmanager
        def serialized():
            state.seen.append('entered')
            try: yield
            finally: state.seen.append('exited')
        def read(path,*,require_current=False):
            if require_current and state.current.release!=path:
                raise ValueError('not current')
            return x if path==x.release else y
        def check(*args):
            self.assertEqual(args,(UNIT,ACTOR,WRAPPER,drop_in()))
            return True
        def restart(unit):
            self.assertEqual(unit,UNIT)
            self.assertTrue(state.settled)
            state.seen.append('restart')
            return True
        def gateway():
            return self.status(state.reply)
        gate=SitesGraphReleaseGate(serialized=serialized,settled=lambda p:state.settled,verify_unit=check,
            verify_import=lambda p,a:(p in (x,y) and a==ACTOR),restart=restart,
            runtime_identity=lambda u,a,p:u==UNIT and a==ACTOR and p==state.current,
            own_gateway_probe=gateway,sleep=lambda _:None,read_identity=read)
        self.assertEqual(gate.prepare(x.release,y.release),(x,y))
        with self.assertRaisesRegex(ValueError,'not cleanly settled'):
            gate.after_settle(y)
        self.assertNotIn('restart',state.seen)
        state.current=x; state.settled=True
        with self.assertRaisesRegex(ValueError,'not verified'):
            gate.after_settle(x)
        self.assertEqual(state.seen.count('restart'),1)
        # These are separate authoritative transition operations, not helper rollback.
        state.current=y
        self.assertTrue(gate.after_settle(y))
        self.assertEqual(state.seen.count('restart'),2)

    def status(self,proof):
        return {'stage':'prod','release_version':proof.tag,'release_generation':proof.generation,
                'global_order':False,'queues':[{'id':x} for x in ('source','maintenance','interactive')]}

    def test_gateway_receipt_rejects_lane_or_mutation_capability(self):
        expected=ProductionGraphIdentity('assertion-memory-v1.2.3','f'*64,Path('/fictional/release'),1,2)
        ok=self.status(expected)
        self.assertTrue(matches_status(ok,expected))
        for bad in ({**ok,'stage':'test'}, {**ok,'release_generation':'b'*64},
                    {**ok,'processing_control':{'can_change':True}},
                    {**ok,'processing_control':None}, {**ok,'queues':[]}):
            with self.subTest(bad=bad):self.assertFalse(matches_status(bad,expected))
