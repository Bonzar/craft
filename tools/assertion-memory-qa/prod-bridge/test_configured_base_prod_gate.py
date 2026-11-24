import hashlib,io,json,sqlite3,unittest
from dataclasses import replace
from configured_base_prod_gate import *

class Gate(unittest.TestCase):
    def setUp(self):
        self.p=Promotion(BASE,Original('assertion-memory-v0.1.1','a'*40,'2','3','4','b'*64),
            'c'*40,'5','6',Content(hashlib.sha256(b'archive').hexdigest()),'workflow_run')
        self.policy=Policy('7','c'*40,'example','repo:Bonzar/craft:environment:'+BASE.prod.environment,'ABCD',frozenset({'8'}))
    def test_stale_control_never_gets_challenge(self):
        with self.assertRaises(ValueError): audience(self.policy,replace(self.p,control_sha='d'*40),'f'*64)
    def test_same_run_one_use_and_changed_receipt(self):
        l=Ledger(sqlite3.connect(':memory:'),lambda *a:None)
        nonce=l.issue(self.policy,self.p,1)['nonce']
        with self.assertRaises(ValueError): l.confirm(self.policy,replace(self.p,test_receipt_id='9'),nonce,'jwt',{},2)
        self.assertEqual(l.confirm(self.policy,self.p,nonce,'jwt',{},2),permit(self.p))
        with self.assertRaises(ValueError):l.confirm(self.policy,self.p,nonce,'jwt',{},2)
        with self.assertRaises(ValueError):l.issue(self.policy,self.p,3)
    def test_writer_not_entered_on_refusal_and_payload_unchanged(self):
        frame={'op':'confirm','intent':intent_document(self.p),'nonce':'f'*64,'token':'fake'}
        def source():return io.BytesIO(json.dumps(frame).encode()+b'\narchive')
        def writer(rev,digest,f): 
            self.assertEqual((rev,digest,f.read()),(self.p.release.revision,self.p.content.sha256,b'archive'))
            return permit(self.p)
        with self.assertRaises(ValueError):
            receive(source(),lambda _: {'status':'rejected'},lambda *_:self.fail('entered PROD writer'))
        self.assertEqual(receive(source(),lambda _:permit(self.p),writer),permit(self.p))
    def test_signed_claim_exact_and_no_old_caller(self):
        p=self.p;q=self.policy;n='f'*64
        claims={'iss':ISSUER,'aud':audience(q,p,n),'sub':q.subject,'repository':REPOSITORY,'repository_id':q.repository_id,
          'repository_owner':'Bonzar','repository_owner_id':str(OWNER_ID),'workflow_ref':BASE.caller,'workflow_sha':q.control_sha,
          'job_workflow_ref':BASE.reusable_path+'@'+REF,'job_workflow_sha':q.control_sha,'sha':q.control_sha,
          'ref':REF,'ref_type':'branch','environment':BASE.prod.environment,'environment_node_id':q.environment_node_id,
          'event_name':p.trigger,'run_attempt':'1','run_id':p.run_id,'runner_environment':'github-hosted','actor_id':'8',
          'iat':100,'nbf':99,'exp':150}
        claims_policy(claims,q,p,n,110)
        self.assert_real_signature(claims,q,p,n)
        for key,value in {'workflow_ref':'old','job_workflow_ref':BASE.reusable_path+'@'+q.control_sha,
            'job_workflow_sha':'a'*40,'workflow_sha':'a'*40,'sha':'a'*40,'event_name':'workflow_dispatch',
            'run_attempt':'2','run_id':'9','environment':'production','actor_id':'9'}.items():
            with self.subTest(key=key),self.assertRaises(ValueError):claims_policy(claims|{key:value},q,p,n,110)

    def assert_real_signature(self,claims,q,p,n):
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding,rsa
        def enc(x):return base64.urlsafe_b64encode(x).decode().rstrip('=')
        def integer(x):return enc(x.to_bytes((x.bit_length()+7)//8,'big'))
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        pub=key.public_key().public_numbers()
        keys={'keys':[{'kid':'qa','kty':'RSA','n':integer(pub.n),'e':integer(pub.e)}]}
        def sign(c):
            hdr=enc(b'{"alg":"RS256","kid":"qa"}')
            bdy=enc(json.dumps(c).encode())
            msg=(hdr+'.'+bdy).encode('ascii')
            return msg.decode()+'.'+enc(key.sign(msg,padding.PKCS1v15(),hashes.SHA256()))
        gate=Ledger(sqlite3.connect(':memory:'))
        nonce=gate.issue(q,p,110)['nonce']
        bound=claims|{'aud':audience(q,p,nonce)}
        with self.assertRaises(ValueError):
            gate.confirm(q,p,nonce,sign(bound|{'job_workflow_sha':'a'*40}),keys,111)
        self.assertEqual(gate.confirm(q,p,nonce,sign(bound),keys,111),permit(p))

    def test_provider_uses_same_intent_and_binds_bytes(self):
        from configured_base_prod_provider import operate
        from unittest.mock import Mock
        calls=[]; nonce='f'*64
        def exchange(cmd,frame,archive):
            self.assertEqual(cmd,'configured-prod-receive')
            self.assertEqual(frame['intent'],intent_document(self.p))
            calls.append(frame['op'])
            if archive is None:return {'status':'ok','nonce':nonce}
            self.assertEqual(archive.read(),b'archive')
            self.assertEqual(frame['token'],'issued')
            return permit(self.p)
        def mint(aud):
            self.assertEqual(aud,audience(self.policy,self.p,nonce))
            return 'issued'
        path=Mock()
        path.open.side_effect=[io.BytesIO(b'archive'),io.BytesIO(b'wrong')]
        self.assertEqual(operate(exchange,self.policy,self.p,path,get_token=mint),permit(self.p))
        with self.assertRaises(ValueError):operate(exchange,self.policy,self.p,path,get_token=mint)
        self.assertEqual(calls,['challenge','confirm'])

    def test_exact_transport_envelope_and_scoped_route(self):
        from contextlib import contextmanager
        from unittest.mock import Mock,patch
        from configured_base_prod_provider import operate, route
        from prod_runner import exchange_factory
        import ssh_transport
        from pathlib import Path
        host='example.test'; scoped='assertion-cfg-base-prod-deploy@'+host
        env={'RELEASE_SSH_HOST':host,'RELEASE_SSH_PORT':'22','RELEASE_SSH_HOST_PIN':'pin',
             'RELEASE_SSH_KEY':'key','RUNNER_TEMP':'/unused'}
        @contextmanager
        def builder(**kw):
            self.assertEqual(kw,{'host':host,'port':'22','account':'assertion-production-deploy',
                                'host_pin':'pin','key':'key','runtime_dir':'/unused'})
            yield ['/usr/bin/ssh','-T','--','assertion-production-deploy@'+host]
        seen=[]; nonce='f'*64
        def wire(argv,command,*,envelope,payload,limit):
            self.assertEqual(argv,['/usr/bin/ssh','-T','--',scoped])
            self.assertEqual((command,limit),('configured-prod-receive',512))
            self.assertTrue(envelope.endswith(b'\n'))
            d=obj(envelope)
            self.assertEqual(d['intent'],intent_document(self.p))
            self.assertEqual(envelope,json.dumps(d,separators=(',',':')).encode('ascii')+b'\n')
            seen.append(d['op'])
            result={'status':'ok','nonce':nonce}
            if payload is not None:
                self.assertEqual((d['token'],d['nonce'],payload.read()),('issued',nonce,b'archive'))
                result=permit(self.p)
            return 0,json.dumps(result).encode('ascii')+b'\n'
        archive=Mock()
        archive.open.return_value=io.BytesIO(b'archive')
        with patch.object(ssh_transport,'route',builder),patch.object(ssh_transport,'exchange',wire),route(env) as argv:
            self.assertEqual(operate(exchange_factory(argv),self.policy,self.p,archive,get_token=lambda _: 'issued'),permit(self.p))
        self.assertEqual(seen,['challenge','confirm'])
