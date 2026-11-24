import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

import prod_service_hook as h
import release_core as core

class Hook(unittest.TestCase):
    def setUp(self):
        self.policy=h.Policy(database='/srv/scoped-prod/db.sqlite3', db_dev=42, db_inode=99,
          mcp_config=str(h.ROOT/'state/http.json'), mcp_token=str(h.ROOT/'state/mcp-token'),
          graph_password=str(h.ROOT/'state/graph-password'), mcp_port=8903,graph_port=8901,
          services_ownership_attested=True,legacy_system_units=['legacy-mcp.service','legacy-graph.service'])

    def test_wrong_db_inode_and_test_path_denied(self):
        self.policy.check()
        with patch.object(h.os,'stat',return_value=Mock(st_mode=stat.S_IFREG|0o600,st_dev=42,st_ino=99)):
            self.policy.check_database()
        with patch.object(h.os,'stat',return_value=Mock(st_mode=stat.S_IFREG|0o600,st_dev=42,st_ino=98)):
            with self.assertRaisesRegex(ValueError,'approved identity'): self.policy.check_database()
        for k,v in [('database','/srv/assertion-release/test/state/fictional.sqlite3'),
                    ('graph_port',8891),('services_ownership_attested',False)]:
            from dataclasses import replace
            with self.assertRaises(ValueError): replace(self.policy,**{k:v}).check()

    def test_old_writer_must_verified_quiesced(self):
        h.require_legacy_quiesced(self.policy,lambda *a,**kw:'MainPID=0\nActiveState=inactive\nLoadState=loaded\n')
        for state in ('MainPID=47\nActiveState=active\nLoadState=loaded\n',
                      'MainPID=0\nActiveState=inactive\nLoadState=not-found\n'):
            with self.assertRaisesRegex(ValueError,'not quiesced'):
                h.require_legacy_quiesced(self.policy,lambda *a,**kw:state)

    def test_pid_listener_never_certifies_another_scope(self):
        good='LISTEN 0 12 127.0.0.1:8903 0.0.0.0:* users:(("python",pid=23,fd=5))'
        h.check_listener(good,8903,23)
        for bad in [good.replace('pid=23','pid=24'), good.replace('127.0.0.1','0.0.0.0'),
                    good+'\n'+good.replace('127.0.0.1','0.0.0.0')]:
            with self.assertRaises(ValueError): h.check_listener(bad,8903,23)

    def test_http_positive_and_negative_only_metadata(self):
        smoke=h.Smoke(self.policy)
        smoke.request=Mock(side_effect=[(401,b''),(200,b'{"result":{"tools":[{}]}}'),(401,b''),(200,b'')])
        smoke.run('x'*32,'y'*32)
        self.assertEqual([c.args[1] for c in smoke.request.call_args_list],
                         ['/memory/mcp','/memory/mcp','/api/v1/graph','/api/v1/session'])
        for c in smoke.request.call_args_list:
            self.assertNotIn('memory_history', str(c.kwargs.get('body')))
        with self.assertRaises(ValueError): smoke.run('x'*32,'x'*32)

    def test_existing_journal_rollback_restores_old_and_resmokes(self):
        with TemporaryDirectory() as root:
            root=Path(root); releases=root/'releases'; releases.mkdir()
            old=releases/('a'*40+'-'+'b'*64); new=releases/('c'*40+'-'+'d'*64)
            for p in (old,new):
                p.mkdir(); (p/'.prepared-v1').write_text('ok\\n'.replace('\\n','\n'))
            core.swap(root,old); events=[]
            def mock_hook(_,action,release):
                events.append((action,release))
                if (action,release)==('smoke',new): raise ValueError('wrong synthetic destination')
            with self.assertRaises(ValueError): core.activate(root,new,None,mock_hook)
            self.assertEqual(core.current_target(root),str(old))
            self.assertEqual(events,[('restart',new),('smoke',new),('restore',old),('smoke',old)])
            self.assertFalse((root/core.PENDING).exists())

if __name__=='__main__': unittest.main()
