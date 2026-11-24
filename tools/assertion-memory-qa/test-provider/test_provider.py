from http.client import HTTPConnection
import hashlib
import shutil
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

import provider as p

TOKEN = '1'*64
HEADERS = {p.HEADER: 'Bearer ' + TOKEN,'Content-Type':'application/json'}
BODY = json.dumps(p.REQUEST)
RELEASE = 'assertion-memory-v0.1.1'

class ProviderTest(unittest.TestCase):
    def test_fixture_canonical_and_read_only(self):
        repo = Path(__file__).resolve().parents[3]
        with TemporaryDirectory() as t:
            file = Path(t)/'new.sqlite3'
            with self.assertRaises(sqlite3.Error):
                p.fixture_exists(file)
            self.assertFalse(file.exists())
            subprocess.run([sys.executable, '-B', 'tools/assertion-memory-qa/synthetic_fixture.py', str(file)],
                cwd=repo, env={'PYTHONPATH':str(repo)}, check=True)
            before = file.read_bytes()
            self.assertTrue(p.fixture_exists(file))
            self.assertEqual(file.read_bytes(), before)
            with sqlite3.connect(file) as db:
                db.execute("UPDATE memory_raw_events SET scope='wrong'")
            self.assertFalse(p.fixture_exists(file))

    def test_wal_under_kernel_readonly_bind(self):
        bubblewrap = shutil.which('bwrap')
        if not bubblewrap:
            self.skipTest('requires bubblewrap')
        ro = [bubblewrap,'--ro-bind','/','/','--']
        if subprocess.run([*ro, '/usr/bin/true'],capture_output=True).returncode:
            self.skipTest('no permission to create read-only mount namespace')
        repo = Path(__file__).resolve().parents[3]
        with TemporaryDirectory() as t:
            dbfile = Path(t)/'in.sqlite3'
            subprocess.run([sys.executable,'-B','tools/assertion-memory-qa/synthetic_fixture.py',str(dbfile)],
                cwd=repo,env={'PYTHONPATH':str(repo)},check=True)
            with sqlite3.connect(dbfile) as db:
                self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone(),('wal',))
                db.execute('CREATE TABLE test_wal(note TEXT)')
                db.commit()
                before = {f.name:f.read_bytes() for f in Path(t).iterdir()}
                self.assertIn('in.sqlite3-shm',before)
                copied = dbfile.with_name('missing.sqlite3')
                shutil.copyfile(dbfile,copied)
                check = """
import os, pathlib, sqlite3, sys
import provider
existing, missing = map(pathlib.Path, sys.argv[1:])
for f in (existing, missing):
    provider.require_readonly_db_mount(f)
assert provider.fixture_exists(existing)
try:
    provider.fixture_exists(missing)
except sqlite3.Error:
    pass
assert not missing.with_name(missing.name+'-shm').exists()
assert not missing.with_name(missing.name+'-wal').exists()
"""
                proc = subprocess.run([*ro, sys.executable,'-B','-c',check,str(dbfile),str(copied)],capture_output=True,text=True)
                self.assertEqual(proc.returncode,0,proc.stderr)
                self.assertEqual(before,{name:(Path(t)/name).read_bytes() for name in before})
                self.assertEqual(set(before)|{copied.name},{f.name for f in Path(t).iterdir()})

    def test_replaced_live_service_rejects(self):
        serial = iter((('one',),('two',)))
        with self.assertRaises(ValueError):
            p.proof(RELEASE,'tag',snapshot=lambda _:next(serial),exists=lambda _:True)

    def test_fixed_unit_membership_rejects_stale_ambiguous_or_aliased_candidate(self):
        uid = os.getuid()
        if uid == 0:
            self.skipTest('expected unprivileged TEST uid')
        with TemporaryDirectory() as t:
            cgroot, procroot = (Path(t)/n for n in ('cgroups','proc'))
            group = f'/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/assertion-test-deploy-graph.service'
            unit = cgroot/group[1:]
            unit.mkdir(parents=True)
            proc = procroot/'4242'
            proc.mkdir(parents=True)
            (unit/'cgroup.procs').write_text('4242\n')
            (proc/'cgroup').write_text('0::'+group+'\n')
            get = lambda: p.unit_process('graph', uid, cgroup_root=cgroot, proc_root=procroot)
            self.assertEqual(get(),(4242,proc))
            (proc/'cgroup').write_text('0::'+group.replace('-graph','-mcp')+'\n')
            with self.assertRaises(ValueError): get()
            (proc/'cgroup').write_text('0::'+group+'\n')
            (unit/'cgroup.procs').write_text('4242\n4243\n')
            with self.assertRaises(ValueError): get()
            (unit/'cgroup.procs').write_text('4242\n')
            moved=unit.with_name('other.service')
            unit.rename(moved)
            unit.symlink_to(moved)
            with self.assertRaises(ValueError): get()

    def test_listener_cannot_be_substituted(self):
        current = Path('/proc')/str(__import__('os').getpid())
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
            self.assertFalse(p.process_listens(current, port))
            s.listen()
            self.assertTrue(p.process_listens(current, port))

    def test_identity_validation(self):
        for invalid in [{}, {'tag':RELEASE,'commit':'a'*40,'archive_sha256':'/tmp/data'},
            {'tag':RELEASE,'commit':'a'*40,'archive_sha256':'b'*64,'db':'elsewhere'}]:
            with self.assertRaises(ValueError): p.expected_identity(json.dumps(invalid))
        tag, where = p.expected_identity(json.dumps({'tag':RELEASE,'commit':'a'*40,'archive_sha256':'b'*64}))
        self.assertEqual((tag,where.parent),(RELEASE,p.RELEASES))

    def test_request_boundary(self):
        count = [0]
        def answer():
            count[0] += 1
            if count[0] == 2: raise ValueError
            return p.proof(RELEASE,'tag',snapshot=lambda _:('stable',),exists=lambda _:True)
        server = p.Server(('127.0.0.1',0), p.handler(hashlib.sha256(TOKEN.encode()).hexdigest(), answer))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method='POST', path=p.ROUTE, body=BODY, headers=HEADERS):
            conn = HTTPConnection(*server.server_address, timeout=3)
            try:
                conn.request(method,path,body,headers)
                reply=conn.getresponse()
                return reply.status, json.loads(reply.read()), reply.getheader('cache-control')
            finally: conn.close()
        try:
            self.assertEqual(request(headers={'Content-Type':'application/json'})[0],401)
            self.assertEqual(request(headers={**HEADERS,p.HEADER:'Bearer '+'2'*64})[0],401)
            self.assertEqual(request(path=p.ROUTE+'?environment=prod')[0],404)
            self.assertEqual(request(method='GET')[0],405)
            self.assertEqual(request(body='{"operation":"memory_environment","operation":"memory_environment","fixture_id":"'+p.FIXTURE+'"}')[0],400)
            self.assertEqual(request(body=json.dumps({**p.REQUEST,'release':RELEASE}))[0],400)
            self.assertEqual(request(body='x'*513)[0],400)
            self.assertEqual(count[0],0)
            status, body, cache=request()
            self.assertEqual((status, body, cache), (200, {'environment':'test','release':RELEASE,'fixture_id':p.FIXTURE,'synthetic':True}, 'private, no-store'))
            self.assertEqual(request()[:2], (503,{'error':'test_unavailable'}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

if __name__=='__main__': unittest.main()
