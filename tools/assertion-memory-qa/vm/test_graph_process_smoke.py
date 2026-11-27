"""Process-to-response proof with an isolated proc fixture, no live service."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from test_sites_graph_browser_smoke import health


class ProcessProof(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.release = self.root/'releases'/('a'*40+'-'+'b'*64)
        self.release.mkdir(parents=True)
        (self.root/'current').symlink_to(self.release)
        self.proc = self.root/'proc'
        self.task = self.proc/'42'
        (self.task/'fd').mkdir(parents=True)
        (self.proc/'self/net').mkdir(parents=True)
        self.table = self.proc/'self/net/tcp'
        self.header = b'  sl  local_address rem_address st\n'
        self.row = b'  0: 0100007F:22BB 00000000:0000 0A 0 0 0 994 0 12345\n'
        self.table.write_bytes(self.header + self.row)
        (self.proc/'self/net/tcp6').write_bytes(self.header)
        (self.task/'cwd').symlink_to(self.release)
        (self.task/'fd/5').symlink_to('socket:[12345]')
        (self.task/'status').write_bytes(b'Uid:\t994\t994\t994\t994\n')
        self.group = b'/user.slice/special.scope/' + health.GRAPH_UNIT.encode()
        (self.task/'cgroup').write_bytes(b'0::' + self.group + b'\n')
        self.stat(1700)

    def stat(self, start):
        (self.task/'stat').write_bytes(b'42 (name with ) parenthesis) S '+b'0 '*18+str(start).encode()+b'\n')

    def observe(self, release):
        def systemctl(argv, **kwargs):
            self.assertEqual(argv[-1], health.GRAPH_UNIT)
            self.assertEqual(argv[1], '--user')
            return SimpleNamespace(stdout=b'MainPID=42\nActiveState=active\nControlGroup='+self.group+b'\n')
        return health.graph_process(release,root=self.root,proc=self.proc,uid=994,systemctl=systemctl)

    def test_exact_process_wraps_actual_probe(self):
        calls = []
        health.with_graph_process(self.release, lambda: calls.append('response'), observe=self.observe)
        self.assertEqual(calls, ['response'])
        self.assertEqual(self.observe(self.release)[:3], (42,1700,12345))

    def test_pid_reuse_between_observations(self):
        with self.assertRaisesRegex(ValueError,'changed during smoke'):
            health.with_graph_process(self.release, lambda: self.stat(1701), observe=self.observe)

    def test_wrong_socket_owner_and_ambiguous_listener(self):
        fd = self.task/'fd/5'
        fd.unlink()
        fd.symlink_to('socket:[222]')
        with self.assertRaisesRegex(ValueError,'does not own listener'):
            self.observe(self.release)
        self.table.write_bytes(self.header + self.row + self.row)
        with self.assertRaisesRegex(ValueError,'not unique'):
            self.observe(self.release)

    def test_other_generation_inactive_or_missing_evidence_fail_closed(self):
        (self.task/'cwd').unlink()
        (self.task/'cwd').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError,'generation mismatch'):
            self.observe(self.release)
        (self.task/'stat').unlink()
        with self.assertRaises(FileNotFoundError):
            self.observe(self.release)

    def test_wrong_uid_or_unit_cgroup_rejected(self):
        (self.task/'status').write_bytes(b'Uid:\t994\t995\t994\t994\n')
        with self.assertRaisesRegex(ValueError,'UID mismatch'):
            self.observe(self.release)
        (self.task/'status').write_bytes(b'Uid:\t994\t994\t994\t994\n')
        (self.task/'cgroup').write_bytes(b'0::/wrong\n')
        with self.assertRaisesRegex(ValueError,'cgroup mismatch'):
            self.observe(self.release)

    def test_non_loopback_or_ipv6_competitor_rejected(self):
        self.table.write_bytes(self.header + self.row.replace(b'0100007F',b'00000000'))
        with self.assertRaisesRegex(ValueError,'not loopback-only'):
            self.observe(self.release)
        self.table.write_bytes(self.header + self.row)
        (self.proc/'self/net/tcp6').write_bytes(self.header + self.row)
        with self.assertRaisesRegex(ValueError,'not loopback-only'):
            self.observe(self.release)
