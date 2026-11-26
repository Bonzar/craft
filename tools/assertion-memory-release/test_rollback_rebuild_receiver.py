"""Signed local fixtures only; no configured issuer, credentials or receiver."""
import base64
from dataclasses import replace
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from github_graph_preview import REPO, REPO_ID, Reject
from rollback_rebuild_origin import RebuiltOrigin
from rollback_rebuild_promotion import RebuiltPromotion, WORKFLOW
from rollback_rebuild_receiver import ENVIRONMENT, ISSUER, OWNER_ID, PREFIX, OneUse, ReceiverPolicy, audience


def encoded(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


class ReceiverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        public=cls.fixture.public_key().public_numbers()
        cls.jwks={'keys':[dict(kid='fixture',alg='RS256',kty='RSA',use='sig',
                               n=encoded(public.n.to_bytes(256,'big')),
                               e=encoded(public.e.to_bytes(3,'big')))]}

    def setUp(self):
        child=REPO+'/.github/workflows/assertion-memory-rollback-rebuild-reusable.yml@'+'b'*40
        self.policy=ReceiverPolicy('a'*40,child,'b'*40,'EN_TEST',f'repo:{REPO}:environment:{ENVIRONMENT}',
                                   'fixture-vm',frozenset({'90'}))
        self.origin=RebuiltOrigin('base','base-v1','assertion-memory-v0.1.25','c'*40,'10','11','d'*40,
                                  '12','e'*64,'f'*64)
        self.permit=RebuiltPromotion(self.origin,'13','14','a'*40)
        self.db=sqlite3.connect(':memory:')
        self.ledger=OneUse(self.db)
        self.nonce=self.ledger.issue(self.policy,self.permit,1000)

    def tearDown(self):
        self.db.close()

    def token(self, nonce=None, **changes):
        p=self.policy
        claims=dict(iss=ISSUER,aud=audience(p,self.permit,nonce or self.nonce),sub=p.subject,repository=REPO,
                    repository_id=str(REPO_ID),repository_owner='Bonzar',repository_owner_id=OWNER_ID,
                    workflow_ref=REPO+'/'+WORKFLOW+'@refs/heads/main',
                    workflow_sha=p.control_sha,job_workflow_ref=p.child_ref,job_workflow_sha=p.child_sha,
                    sha=p.control_sha,ref='refs/heads/main',ref_type='branch',environment=ENVIRONMENT,
                    environment_node_id=p.environment_node,runner_environment='github-hosted',
                    event_name='workflow_dispatch',run_attempt='1',run_id=self.permit.run_id,actor_id='90',
                    iat=1000,nbf=800,exp=1300)
        claims.update(changes)
        head=encoded(json.dumps(dict(alg='RS256',kid='fixture')).encode())
        body=encoded(json.dumps(claims).encode())
        sig=self.fixture.sign((head+'.'+body).encode('ascii'),padding.PKCS1v15(),hashes.SHA256())
        return head+'.'+body+'.'+encoded(sig)

    def consume(self,token=None,permit=None,digest=None):
        return self.ledger.consume(token or self.token(),self.jwks,self.policy,permit or self.permit,self.nonce,
                                   digest or self.origin.payload_sha,1005)

    def test_success_uses_distinct_rebuild_scope(self):
        self.assertTrue(audience(self.policy,self.permit,self.nonce).startswith(PREFIX))
        self.assertEqual(self.consume(),self.permit)

    def test_original_permit_and_original_signed_audience_are_refused(self):
        original={'release':{},'content':{},'control_sha':'a'*40,'run_id':'13',
                  'test_receipt_id':'14','trigger':'workflow_dispatch'}
        with self.assertRaises(Reject):self.consume(permit=original)
        with self.assertRaises(Reject):
            self.consume(token=self.token(aud='urn:assertion-memory:configured:base:promote:v2:'+'0'*64))
        self.consume()

    def test_payload_and_altered_promotion_digests_are_refused(self):
        with self.assertRaises(Reject):self.consume(digest='0'*64)
        for update in (dict(archive_sha='a'*64),dict(payload_sha='a'*64),dict(revision='1'*40),
                       dict(profile='visual'),dict(kind='original')):
            with self.subTest(update=update),self.assertRaises(Reject):
                self.consume(permit=replace(self.permit,origin=replace(self.origin,**update)))
        self.consume()

    def test_signed_wrong_caller_lane_actor_or_attempt_does_not_consume(self):
        for update in (dict(workflow_ref=REPO+'/.github/workflows/configured-base-release.yml@refs/heads/main'),
                       dict(job_workflow_ref=self.policy.child_ref.replace('b'*40,'a'*40)),
                       dict(environment='configured-release-base-test'),dict(environment_node_id='OTHER'),
                       dict(actor_id='91'),dict(repository_owner_id='91'),dict(run_attempt='2'),dict(run_id='15'),dict(exp=1002),
                       dict(event_name='workflow_run'),dict(ref='refs/heads/feature')):
            with self.subTest(update=update),self.assertRaises(Reject):self.consume(self.token(**update))
        self.consume()

    def test_replay_same_and_new_nonce_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'receiver-only.sqlite'
            with sqlite3.connect(path) as db:
                ledger=OneUse(db)
                nonce=ledger.issue(self.policy,self.permit,1000)
                packet=self.token(nonce)
                ledger.consume(packet,self.jwks,self.policy,self.permit,nonce,self.origin.payload_sha,1001)
            with sqlite3.connect(path) as db:
                ledger=OneUse(db)
                with self.assertRaises(Reject):
                    ledger.consume(packet,self.jwks,self.policy,self.permit,nonce,self.origin.payload_sha,1002)
                fresh=ledger.issue(self.policy,self.permit,1002)
                with self.assertRaises(Reject):
                    ledger.consume(self.token(fresh),self.jwks,self.policy,self.permit,fresh,self.origin.payload_sha,1003)

    def test_untrusted_signature_cannot_consume_challenge(self):
        token=self.token()
        head,body,_=token.split('.')
        with self.assertRaises(Reject):self.consume('.'.join((head,body,encoded(bytes(256)))))
        self.consume()


if __name__=='__main__':
    unittest.main()
