"""Hosted disposable actor test: real peer credentials and shared lock paths."""
from contextlib import contextmanager,ExitStack
from dataclasses import asdict
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase,skipUnless
from unittest.mock import patch

OPS=Path(__file__).resolve().parents[3]/'tools/assertion-memory-release/operations/base_browser_terminal'
QA=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(OPS),str(QA)]
import authorizer_cas as cas
import projection_persistence as persistence
import root_terminal_publisher as terminal_wire
from configured_authorizer_binding import serve_exact
from configured_terminal_state import encode,initial,admit,read_exact,commit,pending_request
from guard_lookup import REQUEST
from root_phase_binding import document
import settled_guard_binding as guard
from terminal_lifecycle import TerminalRequest
from terminal_projection import READ,PUBLISH
from transform_settled_guard import _transform
from transition_witness import Admission,Release
del sys.path[:2]

STOCK_FIXTURE=b"""def receive_restart_after_settlement():
    with fence_lock(False):
        verify_settled()
        run([*userctl(False), 'restart', *NEW], timeout=18)
        verify_settled()
"""

def child_reply(action):
    rd,wr=os.pipe()
    pid=os.fork()
    if pid==0:
        os.close(rd)
        signal.alarm(10)
        try:
            try: answer=action()
            except Exception as exc:answer=type(exc).__name__
            os.write(wr,encode(answer))
        finally:
            os.close(wr)
            os._exit(0)
    os.close(wr)
    try:
        return json.loads(os.read(rd,4096))
    finally:
        os.close(rd)
        os.waitpid(pid,0)

@skipUnless(sys.platform=='linux' and hasattr(os,'fork'),'Linux disposable Unix actor fixture')
class RealDispatchAndNativeFence(TestCase):
    def test_checked_in_dispatch_locked_view_and_transformed_guard_across_processes(self):
        actor=os.getuid()
        if actor==0:
            self.skipTest('the fixture never needs or uses a root user')
        with TemporaryDirectory(prefix='terminal-projection-actors-') as d,ExitStack() as clean:
            work=Path(d)
            state=work/'auth';state.mkdir(mode=0o700)
            native=work/'native';native.mkdir()
            releases=native/'releases';releases.mkdir()
            fence=work/'transition-fence';fence.write_bytes(b'fixture')
            stock_manifest=b'0000000000000000  ./fixture.py\n'
            prior=Release('1'*40,'2'*64,sha256(stock_manifest).hexdigest(),'legacy')
            candidate=Release('a'*40,'b'*64,'c'*64,'sidecar')
            admitted=Admission('3','d'*64,candidate)
            current=releases/(prior.revision+'-'+prior.archive);current.mkdir()
            (current/'SHA256SUMS').write_bytes(stock_manifest)
            (native/'current').symlink_to(current)
            switch=work/'base-owned.json'
            switch.write_bytes(encode(dict(binding='dedicated-base-browser-v1',stage='prod',
                                            version=1,retired_graph_unit=guard.GRAPH)))
            switch.chmod(0o644)
            base_state=admit(initial(trusted_prior=prior),verified_admission=admitted)
            cas._write(state,actor,None,base_state)
            for m,key,value in ((cas,'DIRECTORY',state),(cas,'AUTHORIZER_UID',actor),
                                (persistence,'DIRECTORY',state),(persistence,'AUTHORIZER_UID',actor),
                                (terminal_wire,'AUTHOR_UID',actor),(terminal_wire,'DEPLOY_UID',actor),
                                (guard,'ROOT',native),(guard,'SWITCH',switch),(guard,'UID',actor)):
                clean.enter_context(patch.object(m,key,value))
            original_file=guard._file
            clean.enter_context(patch.object(guard,'_file',
                side_effect=lambda path,limit,owner=None,mode=None:original_file(
                    path,limit,owner=actor if owner==0 else owner,mode=mode)))
            restored=TerminalRequest(admitted,prior,'restore')
            forward=TerminalRequest(admitted,prior,'restart')
            event=lambda req:dict(run=admitted.run,nonce=admitted.nonce,phase=req.phase,
                       kind='phase',proof=sha256(document(req)).hexdigest())
            # Seeding fixtures never poses as root over the socket. Persist
            # exact owned records directly with the real existing CAS locks.
            persistence.persist_projection(expected_admission=base_state,expected=None,
                                           replacement=event(forward),identical=False)
            persistence.persist_projection(expected_admission=base_state,expected=event(forward),
                                           replacement=event(restored),identical=False)
            address='\0base-native-projection-'+secrets.token_hex(12)
            listen=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            listen.bind(address);listen.listen(4)
            server=os.fork()
            if server==0:
                try:
                    def phase():
                        same,observed=persistence.read_locked_view(admitted.run,admitted.nonce)
                        if same!=base_state or observed is None:raise ValueError('fixture not active')
                        return observed['phase']
                    while True:
                        peer,_=listen.accept()
                        with peer:
                            try:
                                serve_exact(peer,normal=lambda *_a,**_kw:None,
                                    independently_observed_phase=phase,
                                    protected_marker_active=lambda:True,terminal_projection_enabled=True)
                            except Exception:
                                peer.sendall(b'{"status":"failed-closed"}\n')
                finally:os._exit(1)
            listen.close()
            def stop_server():
                try:os.kill(server,signal.SIGTERM)
                except ProcessLookupError:pass
                os.waitpid(server,0)
            clean.callback(stop_server)

            def wire(raw):
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as c:
                    c.settimeout(3)
                    c.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
                    c.connect(address)
                    c.sendall(raw)
                    c.shutdown(socket.SHUT_WR)
                    return terminal_wire._credentialed_response(c,expected_uid=actor,limit=4096)

            def query(run=admitted.run,nonce=admitted.nonce):
                return json.loads(wire(encode(dict(op=READ,run=run,nonce=nonce))+b'\n'))
            # Real peer UID goes through checked-in dispatcher (_pid_uid is
            # untouched). Production UID labels are NOT asserted in fixture.
            self.assertEqual(query(),{'status':'ok','event':event(restored)})
            self.assertEqual(query('4'),{'status':'rejected'})
            self.assertEqual(json.loads(wire(encode(dict(event(restored),op=PUBLISH))+b'\n')),
                             {'status':'rejected'})
            self.assertEqual(json.loads(wire(encode(dict(op=READ,run=admitted.run,nonce=admitted.nonce))+b'\nextra')),
                             {'status':'rejected'})
            with (state/cas.LOCK).open('rb') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                # This actually enters read_locked_view and hits its flock,
                # rather than only showing the standalone flock primitive.
                self.assertEqual(query(),{'status':'rejected'})
            self.assertEqual(query()['status'],'ok')

            @contextmanager
            def fixture_fence(_):
                with fence.open('rb') as holder:
                    fcntl.flock(holder,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    yield

            def transformed_run():
                calls=[]
                def run(argv,timeout):calls.append([argv,timeout])
                env=dict(fence_lock=fixture_fence, verify_settled=lambda:None,
                         run=run, userctl=lambda _:['fixture-systemctl','--user'],
                         NEW=(guard.BASE,guard.GRAPH), _base_browser_restart_selected=lambda:
                         guard.restart_selected(run=run,userctl=lambda _:['fixture-systemctl','--user'],
                                                active=lambda unit:False))
                source=_transform(STOCK_FIXTURE,sha256(STOCK_FIXTURE).hexdigest())
                self.assertIn(b'_base_browser_restart_selected()',source)
                with patch.object(guard,'projection_selected',return_value=True),patch.object(guard,'_query',side_effect=lambda:wire(REQUEST)),patch.object(guard,'query_projection',side_effect=query):
                    exec(compile(source,'synthetic stock guard','exec'),env)
                    env['receive_restart_after_settlement']()
                return calls
            self.assertEqual(child_reply(transformed_run),
                [[['fixture-systemctl','--user','restart',guard.BASE,guard.GRAPH],18]])
            stale=query()
            def settle_under_existing_fence():
                with fixture_fence(False):
                    receipt=terminal_wire.canonical_terminal(restored,prior)
                    settled=commit(base_state,proven_root_request=restored,terminal_receipt=receipt)
                    cas._write(state,actor,base_state,settled)
                    # Separate guard process competing while writer still
                    # owns native fence cannot consume any detached reply.
                    return child_reply(transformed_run)
            self.assertEqual(child_reply(settle_under_existing_fence),'BlockingIOError')
            self.assertEqual(stale['status'],'ok')
            self.assertEqual(query()['status'],'settled')
            self.assertEqual(child_reply(transformed_run),'ValueError')
