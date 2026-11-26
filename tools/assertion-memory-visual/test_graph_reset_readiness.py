from pathlib import Path
import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('graph_reset_readiness',Path(__file__).with_name('graph_reset_readiness.py'))
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
cspec=importlib.util.spec_from_file_location('graph_reset_credential',Path(__file__).with_name('graph_reset_credential.py'))
c=importlib.util.module_from_spec(cspec);cspec.loader.exec_module(c)


class TestGraphResetReadiness(unittest.TestCase):
    def test_each_lane_preserves_exact_command_and_never_sees_a_token(self):
        for lane,(unit,root,db,port) in g.PROFILES.items():
            cmd=f'{root}/venv/bin/python -B -m apps.graph_viewer.server --source {db} --port {port}'
            rendered=g.render(lane,unit,'[Service]\nExecStart='+cmd+'\n')
            self.assertIn(f'ExecStart={cmd} --database-clear-lane {lane}',rendered)
            self.assertIn('--database-clear-read-token-file /run/assertion-graph-reset-read/'+lane+'/standalone-graph-token',rendered)
            self.assertNotIn('--bridge-read-token-file',rendered)
            self.assertNotIn('Environment=',rendered)

    def test_cross_lane_wrapper_and_authority_have_no_output(self):
        unit,root,db,port=g.PROFILES['test']
        cmd=f'ExecStart={root}/venv/bin/python -B -m apps.graph_viewer.server --source {db} --port {port}'
        for bad in (cmd.replace(port,'8901'),cmd.replace(db,g.PROFILES['prod'][2]),
                    cmd+' --bridge-read-token-file /etc/secret',cmd+' ; /bin/true',
                    cmd+' --host 0.0.0.0',cmd+' --port 8891',cmd+'\n'+cmd,
                    cmd.replace(root,'%n'), cmd+' --database-clear-lane prod'):
            with self.assertRaises(ValueError):g.render('test',unit,bad)
        with self.assertRaises(ValueError):g.render('prod',unit,cmd)

    def test_exact_token_requires_private_file_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)
            p=parent/'source';p.write_bytes(b'a'*64+b'\n');p.chmod(0o600)
            real=os.fstat
            from types import SimpleNamespace
            def root_stat(fd):
                st=real(fd)
                vals={attr:getattr(st,attr) for attr in ('st_dev','st_ino','st_mode','st_nlink','st_size')}
                return SimpleNamespace(st_uid=0,**vals)
            with patch.object(c.os,'fstat',side_effect=root_stat):
                self.assertEqual(c.exact_source(p),b'a'*64)
                p.chmod(0o644)
                with self.assertRaises(ValueError):c.exact_source(p)
                p.chmod(0o600)
                linked=parent/'symlink';linked.symlink_to(p)
                with self.assertRaises(OSError):c.exact_source(linked)
                linked.unlink()
                os.link(p,linked)
                with self.assertRaises(ValueError):c.exact_source(p)
                linked.unlink()
                p.write_bytes(b'a'*64+b'\n\n')
                with self.assertRaises(ValueError):c.exact_source(p)


if __name__=='__main__':unittest.main()
