"""Same-process integration; requires both declarative project modules."""
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = """
import configured_base, hashlib, json, lifecycle, pathlib, sys, tempfile, zipfile
assert lifecycle.WORKFLOW == 'assertion-memory-base.yml'
base_ci=sys.modules['release_ci']
import configured_visual_profile as visual
assert configured_base.BASE.name=='base' and visual.PROFILE.name=='visual'
assert not any(key.startswith(visual._PACKAGE) for key in sys.modules)
with tempfile.TemporaryDirectory() as folder:
    root=pathlib.Path(folder)
    archive=root/'release.zip'
    content=b'fixture'
    revision='a'*40
    with zipfile.ZipFile(archive,'w') as zip:
        zip.writestr('visual.tar',content)
        zip.writestr('receipt.json',json.dumps(dict(source_commit=revision,
            archive_sha256=hashlib.sha256(content).hexdigest(),archive_bytes=len(content),release_digest='b'*64)))
    assert visual.unpack_verified(archive,root,revision).release_digest=='b'*64
assert visual._operation('lifecycle').WORKFLOW=='assertion-memory-visual.yml'
assert visual._operation('lifecycle').ci is not base_ci
assert sys.modules['lifecycle'] is lifecycle and sys.modules['release_ci'] is base_ci
"""

class Imports(unittest.TestCase):
    def test_base_legacy_module_does_not_capture_visual(self):
        tools=Path(__file__).resolve().parent.parent
        paths=[tools/'assertion-memory-qa', tools/'assertion-memory-visual',
               tools/'assertion-memory-release'/'experimental', tools/'assertion-memory-release']
        setup="import sys;sys.path[:0]="+repr([str(path) for path in paths])+"\n"
        subprocess.run([sys.executable,'-I','-B','-c',setup+SCRIPT],check=True,timeout=10)

if __name__=='__main__':
    unittest.main()
