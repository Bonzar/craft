from dataclasses import replace
from io import BytesIO
import json
from unittest import TestCase
from root_terminal_publisher import check_root_terminal,verify_and_publish_root_peer
from terminal_lifecycle import TerminalRequest
from test_terminal_verifier import Verify

class RootPublisher(TestCase):
    def setup_case(self,phase='restart'):
        f=Verify().fixture(phase)
        req=TerminalRequest(f['admission'],f['predecessor'],phase)
        body=check_root_terminal(controller_pending=req,before=f['before'],after=f['after'],smoke=f['smoke'])
        return req,json.loads(body),f

    def test_owner_smoke_is_verified_before_root_frame_and_only_root_cas(self):
        req,body,f=self.setup_case('restore')
        with self.assertRaises(ValueError):
            check_root_terminal(controller_pending=req,before=f['before'],after=f['after'],
                                smoke=replace(f['smoke'],browser_authenticated_ready=False))
        data=lambda:BytesIO(json.dumps({'op':'base-terminal-v1','receipt':body}).encode()+b'\n')
        observed=[]
        def commit(**kw):
            if observed:
                raise ValueError('repeated CAS')
            observed.append(kw)
        lookup=lambda:(b'exact-private-record',req)
        for uid in (986,980):
            with self.assertRaises(ValueError):
                verify_and_publish_root_peer(None,data(),read_exact_pending=lookup,
                     finish_cas=commit,peer_uid=lambda _:uid)
        reply=verify_and_publish_root_peer(None,data(),read_exact_pending=lookup,
                     finish_cas=commit,peer_uid=lambda _:0)
        self.assertEqual(reply['status'],'ok')
        self.assertEqual(observed[0]['expected'],b'exact-private-record')
        self.assertEqual(json.loads(observed[0]['replacement'])['release']['mode'],'legacy')
        with self.assertRaises(ValueError):
            verify_and_publish_root_peer(None,data(),read_exact_pending=lookup,
                     finish_cas=commit,peer_uid=lambda _:0)

    def test_altered_and_multiple_transaction_fields_rejected(self):
        req,body,_=self.setup_case()
        for alter in (dict(body,run='43'),dict(body,phase='restore'),dict(body,cookie='forbidden')):
            request=BytesIO(json.dumps({'op':'base-terminal-v1','receipt':alter}).encode()+b'\n')
            with self.assertRaises(ValueError):
                verify_and_publish_root_peer(None,request,read_exact_pending=lambda:(b'old',req),
                       finish_cas=lambda **_:self.fail('foreign publication'),peer_uid=lambda _:0)

    def test_protected_dispatch_preserves_deploy_protocol_and_denies_new_nonroot_peers(self):
        from unittest.mock import patch
        from root_terminal_publisher import dispatch_existing_authorizer
        peer=object()
        callback=lambda sock:self.assertIs(sock,peer) or 'legacy'
        with patch('os.getuid',return_value=980), patch('os.geteuid',return_value=980):
            self.assertEqual(dispatch_existing_authorizer(peer,normal=callback,
                read_exact_pending=None,finish_cas=None,_uid=lambda _:986),'legacy')
            with self.assertRaises(ValueError):
                dispatch_existing_authorizer(peer,normal=callback,read_exact_pending=None,
                    finish_cas=None,_uid=lambda _:979)
            with patch('os.geteuid',return_value=986), self.assertRaises(ValueError):
                dispatch_existing_authorizer(peer,normal=callback,read_exact_pending=None,
                    finish_cas=None,_uid=lambda _:0)

    def test_kernel_received_frame_is_bound_to_actual_sender(self):
        import os
        import socket
        from root_terminal_publisher import _credentialed_response
        left,right=socket.socketpair()
        with left,right:
            left.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
            right.sendall(b'controlled fixture\n')
            self.assertEqual(_credentialed_response(left,expected_uid=os.getuid()),b'controlled fixture\n')
            right.sendall(b'wrong source fixture\n')
            with self.assertRaises(ValueError):
                _credentialed_response(left,expected_uid=os.getuid()+1)
