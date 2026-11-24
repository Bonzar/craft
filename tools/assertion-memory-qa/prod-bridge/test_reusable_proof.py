import base64
from dataclasses import replace
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import reusable_proof as v

def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')

def field(number):
    return b64(number.to_bytes((number.bit_length()+7)//8,'big'))


class TestReusableProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        numbers=cls.key.public_key().public_numbers()
        cls.jwks={'keys':[dict(kid='fixture',kty='RSA',n=field(numbers.n),e=field(numbers.e))]}

    def setUp(self):
        self.policy=v.Policy('123','b'*40,'synthetic-prod','repo:Bonzar/craft:environment:'+v.ENVIRONMENT,'EN_SYNTHETIC',frozenset({'7'}))
        self.intent=v.Intent('11','12','13','14','15','16','a'*40,'c'*64)
        self.nonce='d'*64
        self.claims={'iss':v.ISSUER,'aud':v.audience(self.policy,self.intent,self.nonce),'sub':self.policy.subject,
            'repository':v.REPOSITORY,'repository_id':self.policy.repository_id,'repository_owner':'Bonzar',
            'repository_owner_id':str(v.OWNER_ID),'workflow_ref':v.CALLER,'ref':v.REF,'ref_type':'branch',
            'job_workflow_ref':v.REUSABLE_PATH+'@'+self.policy.reusable_sha,'job_workflow_sha':self.policy.reusable_sha,
            'environment':v.ENVIRONMENT,'environment_node_id':self.policy.environment_node_id,'event_name':'workflow_dispatch','run_attempt':'1','actor_id':'7','run_id':'11',
            'runner_environment':'github-hosted','workflow_sha':'e'*40,'sha':'e'*40,'iat':2000,'nbf':2000,'exp':2300}

    def token(self, header=None, **updates):
        claims=self.claims|updates
        hdr={'alg':'RS256','kid':'fixture'}|(header or {})
        wire='.'.join(b64(json.dumps(value).encode()) for value in (hdr,claims))
        sig=self.key.sign(wire.encode(),padding.PKCS1v15(),hashes.SHA256())
        return wire+'.'+b64(sig)

    def check(self, intent=None, nonce=None, now=2000, header=None, **updates):
        return v.verify(self.token(header,**updates),self.jwks,self.policy,intent or self.intent,nonce or self.nonce,now)

    def test_allow_updated_caller_main_but_reusable_code_stays_pinned(self):
        self.check()
        self.check(workflow_sha='f'*40,sha='f'*40)

    def test_deny_other_workflow_or_stolen_mint_from_unprotected_job(self):
        for k,value in {'actor_id':'8','workflow_ref':'different','job_workflow_ref':v.REUSABLE_PATH+'@refs/heads/main',
            'job_workflow_sha':'f'*40,'ref':'refs/heads/branch','repository_id':'456','environment':'test',
            'environment_node_id':'EN_REPLACEMENT','run_attempt':'2','event_name':'push','runner_environment':'self-hosted','sha':'f'*40}.items():
            with self.subTest(k=k),self.assertRaisesRegex(ValueError,'identity'):
                self.check(**{k:value})

    def test_deny_different_nonce_test_build_release_and_exact_archive(self):
        for k,value in {'test_run_id':'91','test_receipt_id':'92','artifact_id':'93','build_run_id':'94',
                        'release_id':'95','revision':'d'*40,'sha256':'b'*64}.items():
            with self.subTest(k=k),self.assertRaisesRegex(ValueError,'audience'):
                self.check(intent=replace(self.intent,**{k:value}))
        with self.assertRaisesRegex(ValueError,'audience'):
            self.check(nonce='e'*64)

    def test_documented_github_nbf_backdating_does_not_extend_validity(self):
        for nbf in (2000,1940,1939,1400):
            with self.subTest(nbf=nbf):
                self.check(nbf=nbf)
        for now, updates in (
            (2000,{'nbf':1399}),
            (2000,{'nbf':2016}),
            (2000,{'iat':2016,'nbf':2000}),
            (2301,{'nbf':1400,'exp':2600}),
            (2000,{'nbf':1400,'exp':2601}),
            (2300,{'nbf':1400}),
        ):
            with self.subTest(now=now,updates=updates),self.assertRaisesRegex(ValueError,'timing'):
                self.check(now=now,**updates)

        with self.assertRaisesRegex(ValueError,'audience'):
            self.check(nbf=1400,aud='different')
        from cryptography.exceptions import InvalidSignature
        payload=self.token(nbf=1400).split('.')
        signature=base64.urlsafe_b64decode(payload[2]+'='*(-len(payload[2])%4))
        payload[2]=b64(bytes([signature[0]^1])+signature[1:])
        with self.assertRaises(InvalidSignature):
            v.verify('.'.join(payload),self.jwks,self.policy,self.intent,self.nonce,2000)

    def test_deny_expired_or_untrusted_key_location(self):
        with self.assertRaisesRegex(ValueError,'timing'):
            self.check(now=2300)
        with self.assertRaisesRegex(ValueError,'header'):
            self.check(header={'jku':'https://untrusted.invalid/jwks'})

    def test_ledger_exact_one_use_after_restart_even_with_new_nonce(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'authorization.sqlite'
            db=sqlite3.connect(path)
            ledger=v.OneUse(db)
            nonce=ledger.issue(self.policy,self.intent,2000)
            token=self.token(aud=v.audience(self.policy,self.intent,nonce))
            self.assertEqual(ledger.verify_and_consume(
                token,self.jwks,self.policy,self.intent,nonce,2000),
                {'environment':'production','revision':self.intent.revision,'sha256':self.intent.sha256})
            db.close()
            db=sqlite3.connect(path)
            self.addCleanup(db.close)
            ledger=v.OneUse(db)
            with self.assertRaisesRegex(ValueError,'consumed challenge'):
                ledger.verify_and_consume(token,self.jwks,self.policy,self.intent,nonce,2000)
            newer=ledger.issue(self.policy,self.intent,2000)
            token=self.token(aud=v.audience(self.policy,self.intent,newer))
            with self.assertRaisesRegex(ValueError,'run already consumed'):
                ledger.verify_and_consume(token,self.jwks,self.policy,self.intent,newer,2000)

    def test_wrong_intent_does_not_burn_valid_challenge(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        ledger=v.OneUse(db)
        nonce=ledger.issue(self.policy,self.intent,2000)
        token=self.token(aud=v.audience(self.policy,self.intent,nonce))
        with self.assertRaisesRegex(ValueError,'audience'):
            ledger.verify_and_consume(token,self.jwks,self.policy,replace(self.intent,test_run_id='88'),nonce,2000)
        ledger.verify_and_consume(token,self.jwks,self.policy,self.intent,nonce,2000)


if __name__=='__main__':
    unittest.main()
