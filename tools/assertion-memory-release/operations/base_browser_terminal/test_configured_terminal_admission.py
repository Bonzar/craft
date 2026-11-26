from dataclasses import replace
import base64
import json
from unittest import TestCase
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from configured_base_prod_gate import (BASE,Original,Content,Promotion,Policy,REPOSITORY,
                                       ISSUER,OWNER_ID,REF,audience,verify)
from configured_terminal_admission import signed_audience,verify_admission
from transition_witness import Release

def enc(data):return base64.urlsafe_b64encode(data).decode().rstrip('=')
def integer(number):return enc(number.to_bytes((number.bit_length()+7)//8,'big'))

class Auth(TestCase):
    def test_exact_v3_signature_reuses_old_claims_and_separates_key_scope(self):
        candidate=Release('a'*40,'b'*64,'d'*64,'sidecar')
        p=Promotion(BASE,Original('assertion-memory-v0.1.1','a'*40,'2','3','4','b'*64),
                 'c'*40,'5','6',Content(candidate.archive),'workflow_run')
        q=Policy('7','c'*40,'example','repo:Bonzar/craft:environment:'+BASE.prod.environment,'ABCD',frozenset({'8'}))
        nonce='f'*64
        root={'iss':ISSUER,'aud':signed_audience(q,p,nonce,candidate),'sub':q.subject,
              'repository':REPOSITORY,'repository_id':q.repository_id,'repository_owner':'Bonzar',
              'repository_owner_id':str(OWNER_ID),'workflow_ref':BASE.caller,'workflow_sha':q.control_sha,
              'job_workflow_ref':BASE.reusable_path+'@'+REF,'job_workflow_sha':q.control_sha,'sha':q.control_sha,
              'ref':REF,'ref_type':'branch','environment':BASE.prod.environment,'environment_node_id':q.environment_node_id,
              'event_name':p.trigger,'run_attempt':'1','run_id':p.run_id,'runner_environment':'github-hosted',
              'actor_id':'8','iat':1000,'nbf':910,'exp':1050}
        secret=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        key=secret.public_key().public_numbers()
        jwks={'keys':[dict(kid='fixture',kty='RSA',n=integer(key.n),e=integer(key.e))]}
        def sign(c):
            raw=(enc(b'{"alg":"RS256","kid":"fixture"}')+'.'+enc(json.dumps(c).encode())).encode('ascii')
            return raw.decode()+'.'+enc(secret.sign(raw,padding.PKCS1v15(),hashes.SHA256()))
        result=verify_admission(sign(root),jwks,q,p,nonce,candidate,1010)
        self.assertEqual(result.candidate,candidate)
        for bad in (replace(candidate,mode='legacy'),replace(candidate,manifest='e'*64),
                    replace(candidate,archive='2'*64)):
            with self.assertRaises(ValueError):
                verify_admission(sign(root),jwks,q,p,nonce,bad,1010)
        for bad in (dict(root,actor_id='9'),dict(root,aud=audience(q,p,nonce))):
            with self.assertRaises(ValueError):
                verify_admission(sign(bad),jwks,q,p,nonce,candidate,1010)
        verify(sign(dict(root,aud=audience(q,p,nonce))),jwks,q,p,nonce,1010)
