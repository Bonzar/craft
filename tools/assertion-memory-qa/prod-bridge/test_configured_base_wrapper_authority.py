"""Base host policy regression: no old wire changes and no mixed authority."""
import base64
from dataclasses import replace,asdict
import hashlib
import json
import unittest
from configured_base_test_gate import Intent, Policy as TestPolicy, audience as test_audience, verify as verify_test
from configured_base_prod_gate import (BASE,Content,Original,Promotion,Policy,audience,claims_policy,
    ISSUER,REPOSITORY,OWNER_ID,REF,WRAPPER)

def canonical(d):
    return json.dumps(d,sort_keys=True,separators=(',',':')).encode('ascii')

class WrapperAuthority(unittest.TestCase):
    def setUp(self):
        self.pin='d'*40;self.run_commit='c'*40;self.nonce='f'*64
        self.test=Intent('5',self.run_commit,'workflow_run','assertion-memory-v0.1.1',
                         '2','3','4','a'*40,'e'*64)
        self.prod=Promotion(BASE,Original(self.test.tag,'a'*40,'2','3','4','b'*64),
                            self.run_commit,'5','6',Content('e'*64),'workflow_run')
        self.base={'iss':ISSUER,'repository':REPOSITORY,'repository_id':'1260097465','repository_owner':'Bonzar',
                   'repository_owner_id':str(OWNER_ID),'workflow_ref':WRAPPER,'workflow_sha':self.run_commit,
                   'job_workflow_ref':BASE.reusable_path+'@'+self.pin,'job_workflow_sha':self.pin,
                   'sha':self.run_commit,'ref':REF,'ref_type':'branch','event_name':'workflow_run','run_attempt':'1',
                   'run_id':'5','runner_environment':'github-hosted','actor_id':'8','iat':1000,'nbf':910,'exp':1050}
    def test_pilot_aud_exact_and_wrapper_separate(self):
        tp=TestPolicy(self.run_commit,'ABCD','example')
        bundle={'schema':1,'stage':'test','profile':'base','attempt':1,'intent':asdict(self.test),
                'environment':BASE.test.environment,'reusable_sha':self.run_commit,
                'environment_node_id':'ABCD','vm_id':'example','repository_id':'1260097465','nonce':self.nonce}
        self.assertEqual(test_audience(tp,self.test,self.nonce),
          'urn:assertion-memory:configured:base:test:v1:'+hashlib.sha256(canonical(bundle)).hexdigest())
        self.assertNotEqual(test_audience(tp,self.test,self.nonce),
          test_audience(replace(tp,mode='pinned_wrapper'),self.test,self.nonce))
        with self.assertRaises(ValueError):test_audience(replace(tp,reusable_sha=self.pin),self.test,self.nonce)
    def test_prod_wrapper_exact_bound_and_no_pilot_mix(self):
        p=Policy('1260097465',self.pin,'example','repo:'+REPOSITORY+':environment:'+BASE.prod.environment,
                 'ABCD',frozenset({'8'}),'pinned_wrapper')
        a=audience(p,self.prod,self.nonce)
        b=self.base|{'aud':a,'sub':p.subject,'environment':BASE.prod.environment,'environment_node_id':'ABCD'}
        claims_policy(b,p,self.prod,self.nonce,1010)
        for field,val in {'workflow_ref':BASE.caller,'job_workflow_ref':BASE.reusable_path+'@'+REF,
            'job_workflow_sha':'1'*40,'workflow_sha':'1'*40,'sha':'1'*40,'environment':BASE.test.environment}.items():
            with self.subTest(field=field),self.assertRaises(ValueError):claims_policy(b|{field:val},p,self.prod,self.nonce,1010)
        with self.assertRaises(ValueError):claims_policy(b,replace(p,mode='pilot'),self.prod,self.nonce,1010)
    def test_tests_real_rs256_rejects_wrapper_pair_mixing(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding,rsa
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048);pub=key.public_key().public_numbers()
        enc=lambda x:base64.urlsafe_b64encode(x).decode().rstrip('=')
        integer=lambda x:enc(x.to_bytes((x.bit_length()+7)//8,'big'))
        keys={'keys':[{'kid':'qa','kty':'RSA','n':integer(pub.n),'e':integer(pub.e)}]}
        def sign(c):
            msg=(enc(b'{"alg":"RS256","kid":"qa"}')+'.'+enc(canonical(c))).encode('ascii')
            return msg.decode()+'.'+enc(key.sign(msg,padding.PKCS1v15(),hashes.SHA256()))
        p=TestPolicy(self.pin,'ABCD','example',mode='pinned_wrapper')
        b=self.base|{'aud':test_audience(p,self.test,self.nonce),'sub':'repo:'+REPOSITORY+':environment:'+BASE.test.environment,
                     'environment':BASE.test.environment,'environment_node_id':'ABCD'}
        verify_test(sign(b),keys,p,self.test,self.nonce,1010)
        for field,val in {'workflow_ref':BASE.caller,'job_workflow_ref':BASE.reusable_path+'@'+REF,
            'job_workflow_sha':'1'*40,'sha':'1'*40,'environment':BASE.prod.environment}.items():
            with self.subTest(field=field),self.assertRaises(ValueError):verify_test(sign(b|{field:val}),keys,p,self.test,self.nonce,1010)
