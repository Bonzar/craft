from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

import release_core
from base_forward_native import BaseNativeBinding, ConfiguredBaseNativeBridge
from forward_release_checkpoints import NativeAttemptCheckpoints, Published
from forward_release_receiver import Request
from forward_release_train import FixApproval, Ledger, Reject
from test_forward_release_train import art, cand, witness


class BaseReceiverJoin(unittest.TestCase):
    def test_verified_intent_native_switch_test_run_then_prod(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            q=NativeAttemptCheckpoints(Ledger(sqlite3.connect(root/'native.db'),lambda:1000),lambda _:nullcontext())
            legacy=art(cand('0.1.36','a'),10)
            A=art(cand('1.0.0','b'),11)
            B=replace(art(cand('1.1.0','c'),12),digest='c'*64)
            q.ledger.initialize(witness('prod',legacy))
            by_digest={a.digest:a for a in (A,B)}
            by_release={a.release_id:a for a in (A,B)}
            stage_roots={}
            effects=[]
            targets={}
            for stage in ('test','prod'):
                r=root/stage
                (r/'releases').mkdir(parents=True)
                legacy_entry=r/'releases'/('a'*64)
                legacy_entry.mkdir()
                (legacy_entry/'.prepared-v1').write_text('ok\n')
                (r/'current').symlink_to(legacy_entry)
                stage_roots[stage]=(r,None)
                for a in (A,B):
                    p=r/'releases'/a.digest
                    p.mkdir()
                    (p/'.prepared-v1').write_text('ok\n')
                    targets[p]=a
            def observe(stage,a):
                pointed=(stage_roots[stage][0]/'current').resolve()
                return witness(stage,targets.get(pointed,legacy))
            binding=BaseNativeBinding(q,inspect_prepared=lambda p:targets.get(p),
                          observe_test=lambda a:observe('test',a),observe_prod=lambda a:observe('prod',a))
            def native_deploy(stream,revision,digest,*,root,config,authorize_activation,witness_factory=None):
                a=by_digest[digest]
                self.assertEqual(a.candidate.revision,revision)
                p=root/'releases'/digest
                def hook(_,mode,path):
                    if mode=='restart': effects.append((root.name,a))
                    if mode=='smoke': self.assertEqual((root/'current').resolve(),path)
                return authorize_activation(p,lambda:release_core.activate(root,p,None,hook,
                                             witness=witness_factory(0) if witness_factory else None))
            def observed_release(release_id):
                a=by_release[release_id]
                return Published(a.candidate,a.release_id,True)
            def request(a,stage,run):
                return Request(a,stage,int(run),root/'trusted-fixture')
            def resolve_test(i):
                a=by_release[int(i.release_id)]
                return request(a,'test',i.run),dict(
                    dispatch=lambda r,g:g is None and r.artifact==a,release=observed_release,
                    live_prod=lambda _:witness('prod',legacy),
                    review=lambda _:FixApproval(A,B.candidate,40,41,'a'*64,'open_major_defect_only'))
            def resolve_prod(p):
                a=by_release[int(p.release.release_id)]
                return request(a,'prod',p.run_id),dict(dispatch=lambda r,g:r.artifact==a and g is not None,
                                release=observed_release,approve=lambda _:True),p.validated_test_run
            bridge=ConfiguredBaseNativeBridge(binding,resolve_test=resolve_test,resolve_prod=resolve_prod,
                                              native_deploy=native_deploy,roots=stage_roots)
            def test(a,run):
                return bridge.test_after_verified_intent(SimpleNamespace(tag=a.candidate.tag,release_id=str(a.release_id),
                     revision=a.candidate.revision,archive_sha256=a.digest,run=str(run)),None)
            def prod(a,proof_run):
                return bridge.prod_after_verified_intent(SimpleNamespace(run_id='100', validated_test_run=proof_run,
                     release=SimpleNamespace(tag=a.candidate.tag,release_id=str(a.release_id),revision=a.candidate.revision),
                     content=SimpleNamespace(sha256=a.digest)),None)
            test(A,22)
            test(B,23)
            self.assertEqual(effects,[('test',A),('test',B)])
            with self.assertRaises(Reject):
                prod(A,22)
            with self.assertRaises(Reject):  # Same exact B bytes but another selected TEST run.
                prod(B,22)
            self.assertEqual(effects,[('test',A),('test',B)])
            self.assertEqual(prod(B,23).artifact,B)
            self.assertEqual(effects,[('test',A),('test',B),('prod',B)])
