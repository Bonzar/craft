
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import test_fixture_provider as p

class TestFixtureEvidence(unittest.TestCase):
 def test_identity_rejects_foreign_material(self):
  good='{"tag":"assertion-memory-v0.1.1","commit":"'+'a'*40+'","archive_sha256":"'+'b'*64+'"}'
  tag,path=p.expected_identity(good)
  self.assertEqual(tag,'assertion-memory-v0.1.1')
  self.assertEqual(path.parent,Path('/srv/assertion-release/test/releases'))
  for v in (good.replace('assertion-memory-v','assertion-memory-visual-v'),good.replace('"tag":','"environment":"prod","tag":'),
            good.replace('"commit":','"tag":"assertion-memory-v0.1.2","commit":'),good.replace('"archive_sha256":"b','"archive_sha256":"/')):
   with self.assertRaises(ValueError):p.expected_identity(v)

 def test_mount_evidence_includes_parent_and_sidecars(self):
  fake='/unit/test/fictional.sqlite3'
  visited=[]
  def status(path):
   visited.append(str(path));return SimpleNamespace(f_flag=p.os.ST_RDONLY)
  with patch.object(Path,'exists',return_value=True),patch.object(Path,'resolve',lambda x,**kw:x),patch.object(p.os,'statvfs',side_effect=status):
   p.require_readonly_db_mount(Path(fake))
  self.assertEqual(visited,['/unit/test',fake,fake+'-wal',fake+'-shm'])
  with patch.object(Path,'exists',return_value=True),patch.object(Path,'resolve',lambda x,**kw:x),patch.object(p.os,'statvfs',return_value=SimpleNamespace(f_flag=0)):
   with self.assertRaises(ValueError):p.require_readonly_db_mount(Path(fake))

 def test_wrong_uid_fails_before_state_or_subprocess_inspection(self):
  with patch.object(p.os,'getuid',return_value=0),patch.object(p.subprocess,'check_output',side_effect=AssertionError):
   with self.assertRaises(ValueError):p.live_snapshot(Path('/unit/test/releases'))
  with patch.object(p.os,'getuid',return_value=150),patch.object(p.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='unrelated')),patch.object(p.subprocess,'check_output',side_effect=AssertionError):
   with self.assertRaises(ValueError):p.live_snapshot(Path('/unit/test/releases'))
if __name__=='__main__':unittest.main()
