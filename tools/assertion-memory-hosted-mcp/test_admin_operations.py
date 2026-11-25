"""Synthetic owner-only admin and deliberately local destruction tests."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from admin_operations import AdminError, AdminOperations, MachineRegistry, OwnerContext
from admin_reset import CanonicalReset, OfflineLease
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService


def machine_authorized(header, digests):
    return hashlib.sha256(header.removeprefix(b'Bearer ')).hexdigest() in digests


def digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


class AdminTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name).resolve()
        self.owner = OwnerContext('fictional-owner-session', 'test', 'assertion-memory-v0.1.9', 'a'*64)
        self.registry = MachineRegistry(self.root, 'test', {'personal-mac': digest('a'*64)},
                                        reserved=[digest('e'*64)])
        self.clock = 100
        self.calls = []
        self.admin = AdminOperations(current_context=lambda: self.owner, registry=self.registry,
                                     reset=self.record, clock=lambda: self.clock)

    def record(self, ctx):
        self.calls.append(ctx)
        return True

    def test_enrollment_uses_existing_full_machine_auth_and_no_credential_response(self):
        token = 'c'*64
        fp = digest(token)
        auth = ('Bearer ' + token).encode('ascii')
        self.assertFalse(machine_authorized(auth, self.registry.digests()))
        response = self.admin.enroll(self.owner, {'name': 'new-laptop', 'fingerprint': fp})
        self.assertNotIn(fp, json.dumps(response))
        self.assertNotIn(token, json.dumps(response))
        self.assertTrue(machine_authorized(auth, self.registry.digests()))
        self.assertFalse(machine_authorized(auth, MachineRegistry(self.root, 'prod').digests()))
        self.assertTrue(machine_authorized(auth, MachineRegistry(self.root, 'test', self.registry.static).digests()))
        self.admin.revoke(self.owner, {'name': 'new-laptop'})
        self.assertFalse(machine_authorized(auth, self.registry.digests()))

    def test_reserved_names_digests_no_path_input_and_no_static_revocation(self):
        for name, fp in [('personal-mac', digest('b'*64)), ('other', digest('a'*64)),
                         ('other', digest('e'*64)), ('../other', digest('b'*64)),
                         ('other', 'x'*64)]:
            with self.subTest(name=name), self.assertRaises(AdminError):
                self.admin.enroll(self.owner, {'name': name, 'fingerprint': fp})
        with self.assertRaises(AdminError):
            self.admin.revoke(self.owner, {'name': 'personal-mac'})
        with self.assertRaises(AdminError):
            self.admin.enroll(self.owner, {'name': 'other', 'fingerprint': digest('b'*64), 'stage':'prod'})

    def test_immutable_lane_and_nonowner_context(self):
        other_lane = OwnerContext(self.owner.owner, 'prod', self.owner.release, self.owner.generation)
        other_owner = OwnerContext('different-owner', 'test', self.owner.release, self.owner.generation)
        for value in (other_lane, other_owner, {}, None):
            with self.subTest(value=value), self.assertRaisesRegex(AdminError, 'context_changed'):
                self.admin.devices(value)

    def test_reset_requires_exact_one_time_live_owner_lane_confirmation(self):
        c = self.admin.prepare_reset(self.owner, {})
        request = {'confirmation_id':c['confirmation_id'], 'confirmation':c['phrase']}
        for phrase in ('ОЧИСТИТЬ PROD', 'ОЧИСТИТЬ test', 'ОЧИСТИТЬ TEST '):
            with self.assertRaisesRegex(AdminError, 'confirmation_invalid'):
                self.admin.execute_reset(self.owner, {**request,'confirmation':phrase})
        self.assertFalse(self.calls)
        got = self.admin.execute_reset(self.owner, request)
        self.assertEqual(got,{'stage':'test','reset':True})
        with self.assertRaisesRegex(AdminError, 'confirmation_invalid'):
            self.admin.execute_reset(self.owner, request)
        self.assertEqual(len(self.calls),1)

    def test_expired_replaced_or_previous_release_challenges_do_not_delete(self):
        first = self.admin.prepare_reset(self.owner,{})
        second = self.admin.prepare_reset(self.owner,{})
        with self.assertRaisesRegex(AdminError, 'confirmation_invalid'):
            self.admin.execute_reset(self.owner,{'confirmation_id':first['confirmation_id'], 'confirmation':first['phrase']})
        self.clock += second['expires_in']
        with self.assertRaisesRegex(AdminError, 'confirmation_invalid'):
            self.admin.execute_reset(self.owner,{'confirmation_id':second['confirmation_id'], 'confirmation':second['phrase']})
        third = self.admin.prepare_reset(self.owner,{})
        prior = self.owner
        self.owner = OwnerContext(prior.owner, prior.stage, 'assertion-memory-v0.2.0', 'b'*64)
        with self.assertRaisesRegex(AdminError, 'context_changed'):
            self.admin.execute_reset(prior,{'confirmation_id':third['confirmation_id'], 'confirmation':third['phrase']})
        self.assertFalse(self.calls)

    def test_missing_reset_supervisor_disables_reset(self):
        admin = AdminOperations(current_context=lambda:self.owner, registry=self.registry)
        with self.assertRaisesRegex(AdminError, 'reset_unavailable'):
            admin.prepare_reset(self.owner,{})
        with self.assertRaisesRegex(AdminError, 'reset_unavailable'):
            CanonicalReset(database='invalid', uid=0, gid=0, quiesce=None, clear_auxiliary=None, ready=None)

    def test_registry_untrusted_permissions_or_symlink_are_refused(self):
        p = self.root / 'external'
        p.touch(mode=0o600)
        self.registry.path.symlink_to(p)
        with self.assertRaises(AdminError):
            self.registry.list()
        self.assertEqual(p.stat().st_size,0)
        self.registry.path.unlink()
        self.root.chmod(0o755)
        with self.assertRaises(AdminError):
            self.registry.list()

    def test_real_offline_sqlite_reset_retains_inode_removes_payload_and_no_archive(self):
        p = self.root / 'fictional.sqlite3'
        p.touch(mode=0o600)
        with MemoryStore(p) as store:
            service = ThoughtService(store,None)
            service.capture_message('fixture','m1','alice','user','fictional old raw payload',0,'local-fixture')
            with store.transaction(write=True) as c:
                c.execute('CREATE TABLE fixture_other_queue (secret TEXT)')
                c.execute("INSERT INTO fixture_other_queue VALUES ('synthetic legacy payload')")
        before=(p.stat().st_dev,p.stat().st_ino)
        events=[]
        @contextmanager
        def quiesce(ctx):
            events.append('fence')
            yield OfflineLease(ctx,p,*before)
            events.append('resume')
        def aux(lease):
            events.append('auxiliary');return True
        def ready(ctx, observed):
            events.append('smoke');return observed==before
        reset=CanonicalReset(database=p,uid=os.getuid(),gid=os.getgid(),quiesce=quiesce,
                             clear_auxiliary=aux,ready=ready)
        self.assertTrue(reset(self.owner))
        self.assertEqual(events,['fence','auxiliary','resume','smoke'])
        self.assertEqual((p.stat().st_dev,p.stat().st_ino),before)
        with closing(sqlite3.connect(p)) as c, c:
            self.assertEqual(c.execute('SELECT count(*) FROM memory_raw_events').fetchone(),(0,))
            self.assertEqual(c.execute('SELECT count(*) FROM memory_capture_jobs').fetchone(),(0,))
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='fixture_other_queue'").fetchone())
        self.assertNotIn(b'fictional old raw payload',p.read_bytes())
        self.assertEqual([f.name for f in self.root.iterdir()],['fictional.sqlite3'])

    def test_modified_inode_or_refused_aux_does_not_reset(self):
        p = self.root / 'fictional.sqlite3'
        p.touch(mode=0o600)
        with closing(sqlite3.connect(p)) as c, c:
            c.execute('CREATE TABLE sentinel (n INTEGER)')
        called=[]
        @contextmanager
        def wrong(ctx):
            yield OfflineLease(ctx,p,p.stat().st_dev,p.stat().st_ino+1)
        def aux(lease):
            called.append(lease);return False
        def verify(ctx,got):
            raise AssertionError('not restarted')
        for backend, code in ((wrong,'reset_lease_invalid'),):
            r=CanonicalReset(database=p,uid=os.getuid(),gid=os.getgid(),quiesce=backend,
                             clear_auxiliary=aux,ready=verify)
            with self.assertRaisesRegex(AdminError,code):r(self.owner)
        self.assertFalse(called)
        @contextmanager
        def valid(ctx):
            yield OfflineLease(ctx,p,p.stat().st_dev,p.stat().st_ino)
        r=CanonicalReset(database=p,uid=os.getuid(),gid=os.getgid(),quiesce=valid,
                         clear_auxiliary=aux,ready=verify)
        with self.assertRaisesRegex(AdminError,'reset_auxiliary_unavailable'):r(self.owner)
        with closing(sqlite3.connect(p)) as c, c:
            self.assertEqual(c.execute('SELECT count(*) FROM sentinel').fetchone(),(0,))


if __name__=='__main__':
    unittest.main()
