"""Pin validator tests; no database connection, rows, or seed data."""
import stat
from types import SimpleNamespace
import unittest
from test_fixture_provider import expected_real_database, verify_real_database_file

class RealDatabaseIdentity(unittest.TestCase):
    def test_deny_inode_swap_hardlink_symlink_or_other_owner(self):
        pin=expected_real_database(b'{"device":12,"inode":44,"graph_fixture_flag":true}')
        base=dict(st_mode=stat.S_IFREG|0o640,st_nlink=1,st_uid=500,st_dev=12,st_ino=44)
        verify_real_database_file(SimpleNamespace(**base),pin,500)
        for key,value in [('st_ino',45),('st_dev',13),('st_nlink',2),('st_uid',501),
                          ('st_mode',stat.S_IFLNK|0o640),('st_mode',stat.S_IFREG|0o660)]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                verify_real_database_file(SimpleNamespace(**(base|{key:value})),pin,500)

    def test_schema_is_exact_caller_cannot_choose_path(self):
        self.assertFalse(expected_real_database(b'{"device":12,"inode":44,"graph_fixture_flag":false}')['graph_fixture_flag'])
        for raw in (b'{"device":12,"inode":1,"graph_fixture_flag":"false"}',
                    b'{"device":12,"inode":1,"graph_fixture_flag":true,"database":"/other"}',
                    b'{"device":12,"device":12,"inode":1,"graph_fixture_flag":true}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                expected_real_database(raw)
