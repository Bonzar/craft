import json
import os
import socket
import sqlite3
import threading
import unittest
from dataclasses import asdict

from authorizer import authorize_request, MAX_REQUEST
from reusable_proof import OneUse, audience
import test_reusable_proof as fixture_module


class TestSocket(unittest.TestCase):
    def setUp(self):
        fixture_module.TestReusableProof.setUpClass()
        self.fixture=fixture_module.TestReusableProof()
        self.fixture.setUp()

    def call(self, data, deny=False):
        local, remote=socket.socketpair()
        values={}
        fixture=self.fixture
        def serve():
            with local, sqlite3.connect(':memory:') as db:
                ledger=OneUse(db)
                def key():
                    values['checked_key_source']=True
                    return fixture.jwks
                authorize_request(local,fixture.policy,ledger,os.getuid()+int(deny),keys=key,now=lambda:2000)
                if not deny and data.get('op')=='challenge' and type(data.get('intent')) is dict:
                    authorize_request(local,fixture.policy,ledger,os.getuid(),keys=key,now=lambda:2000)
                    authorize_request(local,fixture.policy,ledger,os.getuid(),keys=key,now=lambda:2000)
        thread=threading.Thread(target=serve)
        thread.start()
        with remote:
            remote.settimeout(2)
            wire=remote.makefile('rwb')
            self.addCleanup(wire.close)
            def query(request):
                wire.write(json.dumps(request).encode()+b'\n')
                wire.flush()
                return json.loads(wire.readline())
            challenge=query(data)
            if challenge.get('status')=='ok':
                nonce=challenge['nonce']
                token=fixture.token(aud=audience(fixture.policy,fixture.intent,nonce))
                packet={'op':'confirm','intent':asdict(fixture.intent),'nonce':nonce,'token':token}
                values['confirm']=query(packet)
                values['replayed']=query(packet)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        return challenge,values

    def test_challenge_confirm_and_replay(self):
        result,values=self.call({'op':'challenge','intent':asdict(self.fixture.intent)})
        self.assertEqual(result['status'],'ok')
        self.assertEqual(values['confirm'],{'status':'ok','environment':'production',
            'revision':self.fixture.intent.revision,'sha256':self.fixture.intent.sha256})
        self.assertEqual(values['replayed'],{'status':'rejected'})

    def test_wrong_peer_and_invalid_framing_fail_closed(self):
        result,values=self.call({'op':'challenge','intent':asdict(self.fixture.intent)},deny=True)
        self.assertEqual(result,{'status':'rejected'})
        self.assertEqual(values,{})
        result,values=self.call({'op':'invalid','padded':'x'*MAX_REQUEST})
        self.assertEqual(result,{'status':'rejected'})
        self.assertEqual(values,{})
