from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import subprocess
import tarfile
from unittest import TestCase, main
from zipfile import ZipFile

import runtime_companion as c
import runtime_switch as switch
import runtime_consumer as consumer
import runtime_receive as receiver
import runtime_joint as joint


class Companion(TestCase):
    def setUp(self):
        self.identity=c.identity('assertion-memory-visual-v0.1.5','a'*40,'123','456','b'*64)
        self.files={name:b'synthetic '+name.encode() for name in c.RUNTIME_FILES}
        self.inner=c.build(self.identity,self.files)

    def test_exact_archive_receipt_and_test_signature_tuple(self):
        sha=c.digest(self.inner)
        self.assertEqual(c.build(self.identity,self.files),self.inner)
        self.assertEqual(c.verify(self.inner,self.identity,sha),self.files)
        self.assertIn(b'visual-runtime-test-v1 ',c.signing_message(self.identity,sha))
        for name,value in {'tag':'assertion-memory-visual-v0.1.6','revision':'c'*40,'release_id':'124',
                           'build_run_id':'457','static_sha':'d'*64}.items():
            other={**self.identity,name:value}
            self.assertNotEqual(c.signing_message(self.identity,sha),c.signing_message(other,sha))
            with self.assertRaises(ValueError):
                c.verify(self.inner,other,sha)
        with self.assertRaises(ValueError):
            c.verify(self.inner+b'x',self.identity,sha)

    def test_visual_signer_is_bound_to_exact_test_release_and_runtime(self):
        with TemporaryDirectory() as tmp:
            key=Path(tmp)/'fixture'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            allowed=Path(tmp)/'allowed'
            allowed.write_text('visual-test '+key.with_suffix('.pub').read_text())
            sha=c.digest(self.inner)
            signature=c.sign(self.identity,sha,key.read_text())
            self.assertEqual(c.verify_signed(self.inner,self.identity,sha,signature,allowed),self.files)
            altered={**self.identity,'static_sha':'c'*64}
            with self.assertRaises(ValueError):
                c.verify_signed(self.inner,altered,sha,signature,allowed)
            allowed.write_text('visual-prod '+key.with_suffix('.pub').read_text())
            with self.assertRaises(ValueError):
                c.verify_signed(self.inner,self.identity,sha,signature,allowed)

    def test_joint_signed_envelope_reaches_activation_only_for_exact_test_pair(self):
        static=b'synthetic static archive'
        expected={**self.identity,'static_sha':c.digest(static)}
        raw=c.build(expected,self.files)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            key=root/'key'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            policy=root/'allowed'
            policy.write_text('visual-test '+key.with_suffix('.pub').read_text())
            signature=c.sign(expected,c.digest(raw),key.read_text())
            command,wire=receiver.assemble(expected,raw,signature,static)
            payload=wire.getvalue()
            calls=[]
            def activate(descriptor, staged, runtime, files):
                actual=staged.read_bytes()
                calls.append((descriptor,actual,runtime,files))
                return 'fixture-success'
            opts=dict(signers=policy,private_workspace=root,activate=activate)
            for lane,stmt,framed in [('prod',command,payload),('test',command,payload[:-1]+b'?'),
                                     ('test',command,payload+b'trailing')]:
                with self.assertRaises(ValueError):
                    receiver.receive(lane,stmt,BytesIO(framed),**opts)
            self.assertEqual(calls,[])
            self.assertEqual(receiver.receive('test',command,wire,**opts),'fixture-success')
            self.assertEqual(calls,[(expected,static,c.digest(raw),self.files)])

    def test_packed_prod_accepts_exact_common_bytes_but_never_test_signature_or_legacy(self):
        static=b'synthetic paired static TAR'
        expected={**self.identity,'static_sha':c.digest(static)}
        legacy=c.build(expected,self.files)
        packed=c.build(expected,self.files,packed=True)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            identities={}
            for stage in ('test','prod'):
                key=root/stage
                subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
                allowed=root/(stage+'-allowed')
                allowed.write_text('visual-'+stage+' '+key.with_suffix('.pub').read_text())
                identities[stage]=key,allowed
            got=[]
            def receive(stage,signature,body,policy,wire_lane=None):
                command,w=receiver.assemble(expected,body,signature,static,stage=wire_lane or stage)
                return receiver.receive(stage,command,w,signers=policy,private_workspace=root,
                    activate=lambda meta,archive,digest,files:got.append((stage,digest,archive.read_bytes())))
            test_key,test_policy=identities['test']
            prod_key,prod_policy=identities['prod']
            test_signature=c.sign(expected,c.digest(packed),test_key.read_text(),stage='test')
            prod_signature=c.sign(expected,c.digest(packed),prod_key.read_text(),stage='prod')
            for sig,body,policy,lane in [(test_signature,packed,prod_policy,None),
              (prod_signature,packed,prod_policy,'test'),
              (c.sign(expected,c.digest(legacy),prod_key.read_text(),stage='prod'),legacy,prod_policy,None)]:
                with self.assertRaises(ValueError):
                    receive('prod',sig,body,policy,lane)
            self.assertEqual(got,[])
            receive('test',test_signature,packed,test_policy)
            receive('prod',prod_signature,packed,prod_policy)
            self.assertEqual(got,[('test',c.digest(packed),static),('prod',c.digest(packed),static)])

    def test_exact_provider_artifact_and_no_interchange_with_static(self):
        buffer=BytesIO()
        with ZipFile(buffer,'w') as out:
            out.writestr('runtime.zip',self.inner)
            out.writestr('runtime.sha256',c.digest(self.inner)+'\n')
        raw=buffer.getvalue()
        meta={'id':879,'name':c.artifact_name(self.identity),'expired':False,'digest':'sha256:'+c.digest(raw),
              'workflow_run':{'id':456,'head_sha':'a'*40}}
        self.assertEqual(c.verify_provider(meta,raw,self.identity,'879'),(self.files,c.digest(self.inner)))
        for changes in ({'name':'assertion-memory-visual-123-'+'a'*40},{'expired':True},
                        {'digest':'sha256:'+'c'*64},{'workflow_run':{'id':457,'head_sha':'a'*40}}):
            with self.assertRaises(ValueError):
                c.verify_provider({**meta,**changes},raw,self.identity,'879')
        with self.assertRaises(ValueError):
            c.verify_provider(meta,raw,self.identity,'880')


    def test_existing_visual_test_lane_stages_only_original_and_refuses_activation(self):
        bundle=BytesIO()
        with ZipFile(bundle,'w') as z:
            z.writestr('runtime.zip',self.inner)
            z.writestr('runtime.sha256',c.digest(self.inner)+'\n')
        body=bundle.getvalue()
        want=self.identity
        meta={'id':879,'name':c.artifact_name(want),'expired':False,'size_in_bytes':len(body),
              'digest':'sha256:'+c.digest(body),'workflow_run':{'id':456,'head_sha':'a'*40,
                  'repository_id':consumer.REPO_ID,'head_repository_id':consumer.REPO_ID}}
        indexed=[{'id':879,'name':meta['name']}]
        paths=[]
        def get(path,dest=None):
            paths.append(path)
            if path.startswith(consumer.REPO+'/actions/runs/456/artifacts?'):
                return {'total_count':len(indexed),'artifacts':indexed}
            if path.endswith('/artifacts/879'):
                return meta
            if path.endswith('/artifacts/879/zip') and dest is not None:
                dest.write_bytes(body)
                return None
            raise AssertionError('Unknown provider call')

        with TemporaryDirectory() as tmp:
            target=Path(tmp)/'verified'
            kw=dict(get=get, original=SimpleNamespace(**want), static_sha=want['static_sha'],destination=target)
            self.assertIsNone(consumer.stage_test_if_required('prod','true',**kw))
            self.assertIsNone(consumer.stage_test_if_required('test','false',**kw))
            self.assertEqual(paths,[])
            self.assertFalse(target.exists())
            indexed.clear()
            with self.assertRaisesRegex(ValueError,'one original'):
                consumer.stage_test_if_required('test','true',**kw)
            self.assertFalse(target.exists())
            indexed.append({'id':879,'name':meta['name']})
            meta['workflow_run']['head_repository_id']=0
            with self.assertRaisesRegex(ValueError,'origin'):
                consumer.stage_test_if_required('test','true',**kw)
            self.assertFalse(target.exists())
            meta['workflow_run']['head_repository_id']=consumer.REPO_ID
            expected,runtime_sha=consumer.stage_test_if_required('test','true',**kw)
            self.assertEqual(expected,want)
            self.assertEqual(runtime_sha,c.digest(self.inner))
            self.assertEqual((target/'runtime.zip').read_bytes(),self.inner)


class Activation(TestCase):
    def setUp(self):
        work=TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root=Path(work.name).resolve()
        (self.root/'runtime.lock').touch(mode=0o600)
        self.old='a'*64
        self.new='b'*64
        for revision in (self.old,self.new):
            p=self.root/'versions'/revision
            p.mkdir(parents=True)
            (p/switch.ENTRY).write_text(revision)
        (self.root/'current').symlink_to('versions/'+self.old)

    def actual(self):
        return (self.root/'current'/switch.ENTRY).read_text()

    def test_new_service_smoke_and_legacy_static_unchanged(self):
        static=self.root/'legacy-static'
        static.write_text('static bytes')
        transitions=[]
        switch.activate(self.root,self.new,restart=lambda:transitions.append(self.actual()),
                        smoke=lambda rev:self.assertEqual(rev,self.actual()))
        self.assertEqual(transitions,[self.new])
        self.assertEqual(static.read_text(),'static bytes')

    def test_failed_smoke_restores_previous_app_or_leaves_fence(self):
        transitions=[]
        def reject_new(rev):
            if rev==self.new:
                raise ValueError('unhealthy')
            self.assertEqual(rev,self.actual())
        with self.assertRaisesRegex(RuntimeError,'previous runtime restored'):
            switch.activate(self.root,self.new,restart=lambda:transitions.append(self.actual()),smoke=reject_new)
        self.assertEqual(transitions,[self.new,self.old])
        self.assertEqual(self.actual(),self.old)
        self.assertFalse((self.root/switch.PENDING).exists())
        def reject_both(_):
            raise ValueError('unhealthy')
        with self.assertRaisesRegex(RuntimeError,'rollback was not verified'):
            switch.activate(self.root,self.new,restart=lambda:None,smoke=reject_both)
        self.assertTrue((self.root/switch.PENDING).is_file())
        with self.assertRaisesRegex(RuntimeError,'requires recovery'):
            switch.activate(self.root,self.new,restart=lambda:None,smoke=reject_both)


    def test_joint_rollback_same_static_digest_and_new_runtime(self):
        visual=self.root/'visual'
        visual.mkdir()
        static='c'*64
        (visual/'releases'/static).mkdir(parents=True)
        (visual/'current').symlink_to('releases/'+static)
        (visual/'deploy.lock').touch(mode=0o600)
        calls=[]
        def restart():
            calls.append((visual/'current').readlink().as_posix()+':'+self.actual())
        def smoke(state):
            if self.actual()==self.new:
                raise ValueError('new runtime failed')
            self.assertEqual(state['runtime'],'versions/'+self.old)
            self.assertEqual(state['static'],'releases/'+static)
        with self.assertRaisesRegex(RuntimeError,'old pair restored'):
            joint.run(visual,self.root,{'static':'releases/'+static,'runtime':'versions/'+self.new},
                      validate=lambda p:p.name,restart=restart,smoke=smoke)
        self.assertEqual(calls,['releases/'+static+':'+self.new,'releases/'+static+':'+self.old])
        self.assertEqual(self.actual(),self.old)
        self.assertFalse((self.root/joint.JOURNAL).exists())


    def test_joint_stager_validates_real_static_tar_and_restores_old_app(self):
        import sys
        sys.path.append(str(Path(__file__).resolve().parents[1]/'assertion-memory-release'))
        visual=self.root/'visual'
        visual.mkdir()
        (visual/'releases').mkdir()
        (visual/'deploy.lock').touch(mode=0o600)
        assets={'index.html':b'<html/>','assets/index-fixture.js':b'// js',
                'assets/index-fixture.css':b'/* css */','assets/graph.worker-fixture.js':b'// worker'}
        manifest=c.wire({name:c.digest(v) for name,v in assets.items()})
        static=c.digest(manifest)
        old=visual/'releases'/static
        (old/'assets').mkdir(parents=True)
        (old/'manifest.json').write_bytes(manifest)
        (old/'.prepared-v1').write_bytes(b'ok\n')
        for name,value in assets.items():
            (old/name).write_bytes(value)
        (visual/'current').symlink_to('releases/'+static)
        archive=self.root/'visual.tar'
        with tarfile.open(archive,'w') as tar:
            for name,value in {**assets,'manifest.json':manifest}.items():
                member=tarfile.TarInfo(name)
                member.size=len(value)
                tar.addfile(member,BytesIO(value))
        digest=c.digest(archive.read_bytes())
        runtime={'asset-origin.py':b'new code','sites-shell.mjs':b'new UI code'}
        revision='d'*64
        def smoke(state):
            if state['runtime']=='versions/'+revision:
                raise ValueError('runtime readiness failed')
            self.assertEqual(self.actual(),self.old)
        with self.assertRaisesRegex(RuntimeError,'old pair restored'):
            joint.activate_verified(visual,self.root,{'static_sha':digest},archive,revision,runtime,
                                    restart=lambda:None,smoke=smoke)
        self.assertEqual(self.actual(),self.old)
        self.assertEqual((visual/'current').readlink().as_posix(),'releases/'+static)


if __name__=='__main__':
    main()
