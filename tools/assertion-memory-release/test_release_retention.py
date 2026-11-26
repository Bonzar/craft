"""Lifecycle contracts for current-only generated paths; no real roots/keys."""
import fcntl
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import base_payload
import release_core
from release_retention import Retention, Store, lock_identity
import runtime_joint


REV='a'*40
TAG=b'assertion-memory-v0.1.26\n'


def arc(contents, fmt='w', manifest=False):
    out=BytesIO()
    entries=dict(contents)
    if manifest:
        entries['SHA256SUMS']=''.join(f'{sha256(data).hexdigest()}  ./{name}\n'
                              for name,data in entries.items()).encode()
    with tarfile.open(fileobj=out,mode=fmt) as tar:
        for name,data in entries.items():
            item=tarfile.TarInfo(name);item.size=len(data)
            tar.addfile(item,BytesIO(data))
    raw=out.getvalue()
    return sha256(raw).hexdigest(),raw


def base_archive(value):
    return arc({'BASE_RELEASE_TAG':TAG,'REVISION':REV.encode()+b'\n',
         'QA_PROFILE':b'assertion-memory-synthetic-qa-v1\n',
         'prototypes/assertion_memory/http_server.py':b'fixture',
         'apps/graph_viewer/web/dist/index.html':value},fmt='w:gz',manifest=True)


def visual_archive(value):
    files={'index.html':value,'assets/index-fixture.js':b'//js',
           'assets/index-fixture.css':b'/*css*/','assets/graph.worker-fixture.js':b'//worker'}
    manifest=(json.dumps({n:sha256(d).hexdigest() for n,d in files.items()},
                         sort_keys=True,separators=(',',':'))+'\n').encode()
    digest,raw=arc({**files,'manifest.json':manifest})
    return (digest,raw,sha256(manifest).hexdigest())


class CurrentOnly(unittest.TestCase):
    def roots(self,name,visual=False):
        work=TemporaryDirectory();self.addCleanup(work.cleanup)
        root=Path(work.name).resolve()/name;root.mkdir();(root/'releases').mkdir()
        (root/'deploy.lock').touch(mode=0o600)
        state=root/'state';state.mkdir()
        for item in ('memory.sqlite3','memory.sqlite3-wal','auth.key','app.log','config'):
            (state/item).write_bytes(b'untouched')
        wheel=root/'wheelhouse';wheel.mkdir();(wheel/'fixture.whl').write_bytes(b'untouched')
        if visual:
            runtime=root/'runtime';runtime.mkdir();(runtime/'versions').mkdir()
            (runtime/'runtime.lock').touch(mode=0o600)
        return root

    def durable(self,root):
        self.assertTrue(all(p.read_bytes()==b'untouched' for p in (root/'state').iterdir()))
        self.assertEqual((root/'wheelhouse'/'fixture.whl').read_bytes(),b'untouched')

    def factory(self,root,stores,decision,minimum=0):
        def bind(fd):
            def proof(kind,active,candidate,meta):
                lock_identity(root,fd,os.geteuid())
                self.assertTrue(active.is_dir() and candidate.is_dir() and active!=candidate)
                self.assertEqual(meta.st_ino,candidate.lstat().st_ino)
                return decision(kind,active,candidate,meta)
            return Retention(root,stores,lock_fd=fd,proof=proof,minimum=minimum)
        return bind

    def deploy_base(self,root,value,hook,decision=lambda *_:True,minimum=0,source=None):
        digest,raw=base_archive(value)
        store=Store('base',base_payload.retention_candidate)
        with patch.object(base_payload,'bindings',return_value=self.factory(root,(store,),decision,minimum)):
            base_payload.deploy(source if source is not None else BytesIO(raw),REV,digest,
                                root=root,config=root/'state',hook=hook)
        return root/'releases'/f'{REV}-{digest}'

    def test_base_keeps_rollback_through_smoke_then_sweeps_preexisting_and_previous(self):
        for lane in ('test','production'):
            with self.subTest(lane=lane):
                root=self.roots(lane)
                a=self.deploy_base(root,b'A',lambda *_:None)
                b=self.deploy_base(root,b'B',lambda *_:None,lambda *_:False)
                self.assertTrue(a.exists())
                outsider=root/'releases'/'unclassified-legacy';outsider.mkdir()
                seen=[]
                def hook(action,path):
                    seen.append(action)
                    if action in ('prepare','restart','smoke'):
                        self.assertTrue(b.exists())  # last rollback until *after* smoke
                        self.assertFalse(a.exists()) # pre-admission sweep
                c=self.deploy_base(root,b'C',hook)
                self.assertEqual(seen,['prepare','restart','smoke'])
                self.assertTrue(c.exists() and outsider.exists())
                self.assertFalse(b.exists())
                self.assertEqual((root/'current').resolve(),c)
                self.durable(root)

    def test_base_failure_reclaims_only_exact_unactivated_candidate_and_denials_defer(self):
        for lane in ('test','production'):
            with self.subTest(lane=lane):
                root=self.roots(lane);prior=self.deploy_base(root,b'old',lambda *_:None)
                def fails(action,path):
                    if action=='prepare':
                        (path/'venv').mkdir()
                        (path/'venv'/'fictional-partial').write_text('partial')
                        raise RuntimeError('prepare')
                for proven in (True,False):
                    with self.assertRaisesRegex(RuntimeError,'prepare'):
                        self.deploy_base(root,b'new',fails,lambda *_:proven)
                    self.assertEqual((root/'current').resolve(),prior)
                    self.assertEqual(len(list((root/'releases').iterdir())),1+(not proven))
                self.durable(root)

    def test_low_space_does_not_consume_an_archive_or_touch_current(self):
        root=self.roots('test')
        self.deploy_base(root,b'old',lambda *_:None)
        source=BytesIO(b'unread')
        with patch('release_retention.os.statvfs',return_value=SimpleNamespace(f_bavail=0,f_frsize=4096,f_favail=100000)):
            with self.assertRaisesRegex(ValueError,'insufficient_headroom'):
                self.deploy_base(root,b'new',lambda *_:None,minimum=1,source=source)
        self.assertEqual(source.tell(),0)
        self.assertEqual(len(list((root/'releases').iterdir())),1)
        self.durable(root)

    def test_refuses_occupancy_links_inodes_and_unsettled_activation(self):
        root=self.roots('test')
        initial=self.deploy_base(root,b'old',lambda *_:None)
        active=self.deploy_base(root,b'new',lambda *_:None,lambda *_:False)
        store=Store('base',base_payload.retention_candidate)
        with (root/'deploy.lock').open('rb') as f:
            with self.assertRaisesRegex(ValueError,'lock_required'):
                self.factory(root,(store,),lambda *_:True)(f.fileno()).sweep('test')
            fcntl.flock(f,fcntl.LOCK_EX)
            engine=self.factory(root,(store,),lambda *_:False)(f.fileno())
            self.assertEqual(engine.sweep('test'),(0,1,0))
            hard=initial/'venv';hard.mkdir()
            os.link(root/'state'/'memory.sqlite3',hard/'fictional_shared')
            engine=self.factory(root,(store,),lambda *_:True)(f.fileno())
            self.assertEqual(engine.sweep('test'),(0,1,0))
            (hard/'fictional_shared').unlink()
            self.assertEqual(engine.sweep('test',dry=True),(1,0,0))
            self.assertTrue(initial.is_dir())
            def nested_race(*_):
                path=initial/'venv'/'new-after-proof'
                path.write_text('unknown')
                return True
            nested=self.factory(root,(store,),nested_race)(f.fileno())
            with self.assertRaisesRegex(ValueError,'tree_changed_after_witness'):
                nested.remove(store,initial)
            self.assertTrue((initial/'venv'/'new-after-proof').is_file())
            self.assertTrue((initial/'SHA256SUMS').is_file())
            (initial/'venv'/'new-after-proof').unlink()
            journal=root/release_core.PENDING
            journal.write_text('unsettled')
            with self.assertRaisesRegex(ValueError,'unsettled'):
                engine.sweep('test')
            journal.unlink()
            old_name=initial.name
            def swap_or_race(*_):
                initial.rename(root/'releases'/'outside-original')
                initial.mkdir()
                return True
            race=self.factory(root,(store,),swap_or_race)(f.fileno())
            self.assertFalse(race.remove(store,root/'releases'/old_name))
            self.assertTrue(initial.is_dir())
            self.assertEqual((root/'current').resolve(),active)
        self.durable(root)

    def visual_deploy(self,root,value,runid,smoke,decision=lambda *_:True):
        digest,raw,staticid=visual_archive(value)
        with TemporaryDirectory() as d:
            archive=Path(d)/'test.tar';archive.write_bytes(raw)
            stores=(Store('visual-static',runtime_joint.retention_static),
                    Store('visual-runtime',runtime_joint.retention_runtime))
            with patch.object(runtime_joint,'retention_config',return_value=self.factory(root,stores,decision)):
                runtime_joint.activate_verified(root,root/'runtime',{'static_sha':digest},archive,runid,
                  {'asset-origin.py':value,'sites-shell.mjs':b'//shell'},restart=lambda:None,smoke=smoke)
        return 'releases/'+staticid,'versions/'+runid

    def visual_baseline(self,root):
        # The pre-existing packed entry may be fully healthy but lack a new
        # signed-generated receipt. It is never retroactively guessed as stale.
        _,raw,old=visual_archive(b'legacy')
        static=root/'releases'/old;static.mkdir()
        with tarfile.open(fileobj=BytesIO(raw)) as tar:
            for member in tar:
                path=static/member.name;path.parent.mkdir(exist_ok=True);path.write_bytes(tar.extractfile(member).read())
        (static/'.prepared-v1').write_bytes(b'ok\n')
        (root/'current').symlink_to('releases/'+old)
        runtime=root/'runtime'/'versions'/('1'*64);runtime.mkdir()
        (runtime/'asset-origin.py').write_bytes(b'legacy code')
        (runtime/'sites-shell.mjs').write_bytes(b'legacy shell')
        (root/'runtime'/'current').symlink_to('versions/'+runtime.name)
        return runtime

    def test_packed_visual_test_and_prod_keep_exact_rollback_pair_then_prune(self):
        for lane in ('test','prod'):
            with self.subTest(lane=lane):
                root=self.roots(lane,True);legacy=self.visual_baseline(root)
                old_static,old_runtime=self.visual_deploy(root,b'A','2'*64,lambda state:None)
                self.assertTrue(legacy.exists())  # no provenance manifest, report only
                def failing(state):
                    if state['runtime']!=old_runtime:
                        self.assertTrue((root/old_static).exists())
                        self.assertTrue((root/'runtime'/old_runtime).exists())
                        raise ValueError('fictional failure')
                with self.assertRaisesRegex(RuntimeError,'old pair restored'):
                    self.visual_deploy(root,b'B','3'*64,failing)
                self.assertEqual((root/'runtime'/'current').readlink().as_posix(),old_runtime)
                self.assertFalse((root/'runtime'/'versions'/('3'*64)).exists())
                new_static,new_runtime=self.visual_deploy(root,b'C','4'*64,lambda state:None)
                self.assertEqual((root/'current').readlink().as_posix(),new_static)
                self.assertEqual((root/'runtime'/'current').readlink().as_posix(),new_runtime)
                self.assertFalse((root/old_static).exists())
                self.assertFalse((root/'runtime'/old_runtime).exists())
                self.assertTrue(legacy.is_dir())
                self.durable(root)

    def test_legacy_visual_runtime_writer_blocks_packed_sweep(self):
        root=self.roots('test',True);self.visual_baseline(root)
        with (root/'runtime'/'runtime.lock').open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with self.assertRaises(BlockingIOError):
                self.visual_deploy(root,b'A','2'*64,lambda state:None)
        self.assertFalse((root/'runtime'/'versions'/('2'*64)).exists())


if __name__=='__main__':
    unittest.main()
