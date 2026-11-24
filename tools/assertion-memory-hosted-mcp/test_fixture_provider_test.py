
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

 def test_wrong_uid_fails_before_state_or_cgroup_inspection(self):
  with patch.object(p.os,'getuid',return_value=0),patch.object(p,'unit_processes',side_effect=AssertionError):
   with self.assertRaises(ValueError):p.live_snapshot(Path('/unit/test/releases'),{})
  with patch.object(p.os,'getuid',return_value=150),patch.object(p.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='unrelated')),patch.object(p,'unit_processes',side_effect=AssertionError):
   with self.assertRaises(ValueError):p.live_snapshot(Path('/unit/test/releases'),{})

 def test_expected_cgroups_isolate_exact_uid_units(self):
  import json
  uid=1234
  prefix='/user.slice/user-1234.slice/user@1234.service/app.slice/'
  info={role:prefix+'assertion-test-deploy-'+role+'.service' for role in p.PORTS}
  self.assertEqual(p.expected_cgroups(json.dumps(info),uid),info)
  for graph in (info['mcp'],info['graph'].replace('1234','1233'),info['graph'].replace('app.slice','../app.slice'),
    info['graph'].replace('app.slice','/app.slice'),info['graph'].replace('test-deploy','prod-deploy')):
   with self.assertRaises(ValueError):p.expected_cgroups(json.dumps({**info,'graph':graph}),uid)

 def test_bounded_readonly_cgroup_and_kernel_membership(self):
  sample='/user.slice/user-1234.slice/user@1234.service/app.slice/assertion-test-deploy-mcp.service'
  with patch.object(Path,'resolve',lambda x,**kw:x),patch.object(p.os,'statvfs',return_value=SimpleNamespace(f_flag=p.os.ST_RDONLY)):
   with patch.object(p,'read',return_value=b'72\n41\n72\n'):
    self.assertEqual(p.unit_processes(sample),[41,72])
   for buf in (b'', b'1\n', b'tools\n', b'72\n'+b'73\n'*129):
    with patch.object(p,'read',return_value=buf):
     with self.assertRaises(ValueError):p.unit_processes(sample)
   with patch.object(p,'read',return_value=('0::'+sample+'\n').encode()):
    self.assertTrue(p.process_in_unit(Path('/proc/41'),sample))
    self.assertFalse(p.process_in_unit(Path('/proc/41'),sample.replace('mcp','graph')))
  with patch.object(Path,'resolve',lambda x,**kw:x),patch.object(p.os,'statvfs',return_value=SimpleNamespace(f_flag=0)):
   with self.assertRaises(ValueError):p.unit_processes(sample)

 def test_only_one_expected_listener_with_helpers(self):
  import contextlib
  role='mcp'; release=Path('/unit/project/releases/fixture')
  cfg=p.MCP_CONFIG
  argv=[str(p.ROOT/'current/venv/bin/python'),'-B','-m','prototypes.assertion_memory.http_server','--config',str(cfg),'']
  cg='/unit/cgroup'
  stat_line='12 (test service) '+' '.join(['S']+['0']*18+['12345'])
  def reader(path,lim):
   return ('\0'.join(argv)).encode() if path.parent.name in ('41','43') else b'helper'
  with patch.object(p,'unit_processes',return_value=[41,42]),patch.object(Path,'stat',return_value=SimpleNamespace(st_uid=1234)), \
   patch.object(p,'process_in_unit',return_value=True),patch.object(p,'read',side_effect=reader),patch.object(Path,'resolve',return_value=release), \
   patch.object(p,'process_listens',return_value=True),patch.object(Path,'read_text',return_value=stat_line), \
   patch.object(p.socket,'create_connection',return_value=contextlib.nullcontext()):
   self.assertEqual(p.listener_evidence(role,8893,release,cg,1234),(41,'12345'))
  with patch.object(p,'unit_processes',return_value=[41,43]),patch.object(Path,'stat',return_value=SimpleNamespace(st_uid=1234)), \
   patch.object(p,'process_in_unit',return_value=True),patch.object(p,'read',side_effect=reader),patch.object(Path,'resolve',return_value=release), \
   patch.object(p,'process_listens',return_value=True),patch.object(Path,'read_text',return_value=stat_line):
   with self.assertRaises(ValueError):p.listener_evidence(role,8893,release,cg,1234)
if __name__=='__main__':unittest.main()

