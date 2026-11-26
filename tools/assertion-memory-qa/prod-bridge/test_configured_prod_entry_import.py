"""No-live import smoke: isolated interpreter, exact checked-in modules, VM-shaped routes."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
PROBE=r"""
import importlib.abc, importlib.util, pathlib, sys
root=pathlib.Path(sys.argv[1])
qa=root/'tools/assertion-memory-qa'
rel=root/'tools/assertion-memory-release'
entry=qa/'configured-base-prod/receiver-entry.py'
auth='/etc/assertion-release/production/authorization'
shared='/etc/assertion-release/shared'
mapping={auth:qa/'prod-bridge',shared:rel}
class VmPath(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name not in {'receive_authorized','receiver_gate','reusable_proof','authorizer','base_payload','release_core','release_retention'}:
            return None
        for directory in sys.path:
            if directory not in mapping:
                continue
            file=mapping[directory]/(name+'.py')
            if file.is_file():
                return importlib.util.spec_from_file_location(name,file)
        return None
sys.meta_path.insert(0,VmPath())
sys.path.append(str(qa))
source=entry.read_bytes()
namespace={'__file__':str(entry),'__name__':'_import_smoke'}
exec(compile(source,str(entry),'exec'),namespace)
assert namespace['activate_exact'].__module__=='receive_authorized'
assert pathlib.Path(sys.modules['base_payload'].__file__)==rel/'base_payload.py'
assert pathlib.Path(sys.modules['release_core'].__file__)==rel/'release_core.py'
assert pathlib.Path(sys.modules['release_retention'].__file__)==rel/'release_retention.py'
assert pathlib.Path(sys.modules['receive_authorized'].__file__)==qa/'prod-bridge'/'receive_authorized.py'
"""

class Imports(unittest.TestCase):
    def test_configured_prod_entry_imports_legacy_and_shared_modules(self):
        result=subprocess.run([sys.executable,'-I','-B','-c',PROBE,str(ROOT)],
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
