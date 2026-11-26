"""Real Unix and shared flock isolation, no creds, paths, or host effects."""
from hashlib import sha256
import fcntl
import json
import os
import socket
from unittest import TestCase,skipUnless
from pathlib import Path
import sys
OPS=Path(__file__).resolve().parents[3]/'tools/assertion-memory-release/operations/base_browser_terminal'
sys.path.insert(0,str(OPS))
from unittest.mock import Mock,patch

from root_terminal_publisher import _credentialed_response
from terminal_projection import dispatch,READ,PUBLISH
from configured_terminal_state import admit,initial,encode
from transition_witness import Admission,Release
import projection_client,settled_guard_binding
sys.path.pop(0)
# Test imports exact checked-in release modules, never host paths.

class ProjectionSocket(TestCase):
    def test_actual_unix_kernel_uid_auth_and_no_snapshot_leak(self):
        previous=Release('1'*40,'2'*64,'3'*64,'legacy')
        candidate=Release('a'*40,'b'*64,'c'*64,'sidecar')
        signed=Admission('1','d'*64,candidate)
        state=admit(initial(trusted_prior=previous),verified_admission=signed)
        event=dict(run='1',nonce='d'*64,phase='restart',kind='phase',proof='e'*64)
        roots=[]
        def call(operation,uid,active=True):
            client,server=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
            with client,server:
                client.settimeout(2);server.settimeout(2)
                client.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
                client.sendall(encode(operation)+b'\n')
                client.shutdown(socket.SHUT_WR)
                with server.makefile('rb') as f:
                    try:
                        result=dispatch(f.readline(1025),verified_peer_uid=uid,active=active,
                            read_admission=lambda:state,read_projection=lambda *_:None,
                            persist_projection=lambda **kw:roots.append(kw),
                            read_locked_view=lambda *_:(state,event))
                    except ValueError:
                        result={'status':'rejected'}
                server.sendall(encode(result)+b'\n')
                return json.loads(_credentialed_response(client,expected_uid=os.getuid(),limit=1024))
        native=dict(op=READ,run='1',nonce='d'*64)
        self.assertEqual(call(native,986),dict(status='ok',event=event))
        self.assertEqual(call(native,980),{'status':'rejected'})
        self.assertEqual(call(native,986,False),{'status':'rejected'})
        self.assertEqual(call(dict(event,op=PUBLISH),986),{'status':'rejected'})
        self.assertEqual(call(dict(event,op=PUBLISH,private_snapshot={}),0),{'status':'rejected'})
        self.assertFalse(roots)
        self.assertEqual(call(dict(event,op=PUBLISH),0)['status'],'ok')
        self.assertEqual(len(roots),1)
        client,server=socket.socketpair()
        with client,server:
            client.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
            server.sendall(b'{"status":"ok"}\n')
            with self.assertRaises(ValueError):
                _credentialed_response(client,expected_uid=os.getuid()+1,limit=1024)

    @skipUnless(hasattr(os,'memfd_create'),'Linux shared flock fixture')
    def test_real_shared_flock_blocks_concurrent_settle_without_another_lock(self):
        original=os.memfd_create('projection-test-no-host-file')
        reader=os.open('/proc/self/fd/'+str(original),os.O_RDONLY)
        writer=os.open('/proc/self/fd/'+str(original),os.O_RDWR)
        try:
            fcntl.flock(reader,fcntl.LOCK_SH|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(writer,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fcntl.flock(reader,fcntl.LOCK_UN)
            fcntl.flock(writer,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(reader,fcntl.LOCK_SH|fcntl.LOCK_NB)
        finally:
            for fd in (writer,reader,original):os.close(fd)

    def test_stock_non_selection_no_socket_and_guard_denies_old_settled_result(self):
        selected,_exchange=projection_client.selected,projection_client._exchange
        with patch('pathlib.Path.lstat',side_effect=FileNotFoundError),patch('socket.socket') as sock:
            self.assertFalse(selected())
            with self.assertRaises(ValueError):_exchange({'op':READ})
        sock.assert_not_called()

        guard=settled_guard_binding
        previous=Release('1'*40,'2'*64,'3'*64,'legacy')
        candidate=Release('a'*40,'b'*64,'c'*64,'sidecar')
        from dataclasses import asdict
        witness=encode({'known':asdict(previous),'pending':{'run':'1','nonce':'d'*64,
            'release':asdict(candidate),'phase':'restore'}})
        active=Mock()
        with patch.object(guard,'projection_selected',return_value=True),patch.object(guard,'_query',return_value=witness),patch.object(guard,'query_projection',return_value={'status':'settled','phase':'restore','proof':'1'*64}):
            with self.assertRaises(ValueError):
                guard.restart_selected(run=active,userctl=Mock(),active=Mock())
        active.assert_not_called()
