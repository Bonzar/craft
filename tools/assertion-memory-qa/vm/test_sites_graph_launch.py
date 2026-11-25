import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('launcher',HERE/'test-sites-graph-launch.py')
launch=importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class Launcher(unittest.TestCase):
    def test_physical_binding_candidate_and_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=Path(d).resolve()
            root=fixture/'test'
            (root/'releases').mkdir(parents=True)
            wrapper=fixture/'trusted'/'start.py'
            wrapper.parent.mkdir()
            wrapper.touch()
            versions=[]
            for char in 'ab':
                immutable=root/'releases'/(char*40+'-'+char*64)
                (immutable/'venv'/'bin').mkdir(parents=True)
                (immutable/'venv'/'bin'/'python').symlink_to(sys.executable)
                (immutable/'REVISION').write_text(char*40+'\n')
                (immutable/'.prepared-v1').write_bytes(b'ok\n')
                versions.append(immutable)
            current=root/'current'
            for physical in [*versions,versions[0]]:
                current.unlink(missing_ok=True)
                current.symlink_to(physical)
                argv,found=launch.command(root,wrapper,os.getuid(),trusted_owner=os.getuid())
                self.assertEqual(found,physical)
                self.assertEqual(argv,[str(physical/'venv'/'bin'/'python'),'-s','-B',str(wrapper)])
            current.unlink()
            current.symlink_to(versions[0].relative_to(root))
            with self.assertRaises(ValueError):
                launch.command(root,wrapper,os.getuid(),trusted_owner=os.getuid())


if __name__=='__main__':
    unittest.main()
