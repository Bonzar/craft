"""Shell trust-boundary fixture; fake CLIs, no runtime or network."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

HERE=Path(__file__).resolve().parent
MAN="3dafba6ea29fa67f90e74f649383eb2ae3d5d40407f50db0e0ef5a1d7d59a008"
CFG="f3e1eee55ce80142e1dd9f00251dfe49ebeed80437ce49e3c9077403558fcc32"


class LocalOCI(unittest.TestCase):
    def execute(self, scenario):
        with TemporaryDirectory() as d:
            root=Path(d)
            (root/"layout").mkdir()
            code={
                "skopeo":"""echo "skopeo $*" >>"$LOG"
case "$*" in
  *oci:*) [[ "$1" == copy ]] || printf %s SRC ;;
  *containers-storage:*) printf %s "${SCENARIO:-DST}" ;;
esac
""",
                "sha256sum":"""if [[ "$(cat)" == BAD ]]; then printf "mismatch  -\\n"; else printf "%s  -\\n" "$MAN"; fi
""",
                "podman":"""printf 'podman %s\\n' "$*" >>"$LOG"
if [[ "$2" == image ]]; then
  printf 'sha256:%s %s linux amd64\\n' "$MAN" "$CFG"
fi
""",
            }
            for name,script in code.items():
                p=root/name
                p.write_text("#!/bin/bash\nset -eu\n"+script)
                p.chmod(0o700)
            env=os.environ | {"PATH":f"{root}:{os.environ['PATH']}",
                "LOG":str(root/"calls"),"SCENARIO":scenario,"MAN":MAN,"CFG":CFG}
            run=subprocess.run(["bash",str(HERE/"hosted_035_import_local_oci.sh"),str(root/"layout")],
                               env=env,capture_output=True)
            return run.returncode,(root/"calls").read_text()

    @unittest.skipIf(os.getuid()==0,"rootless fixture requires non-root test process")
    def test_exact_only_then_isolated_probe(self):
        code,calls=self.execute("DST")
        self.assertEqual(code,0)
        self.assertIn("skopeo copy --preserve-digests oci:",calls)
        self.assertIn("containers-storage:localhost/rollback-base035:qualification",calls)
        self.assertIn("podman --remote=false run --rm --pull=never --network=none",calls)
        self.assertIn(CFG,calls)
        for forbidden in ("docker://","--tls-verify","--privileged"):
            self.assertNotIn(forbidden,calls)

    @unittest.skipIf(os.getuid()==0,"rootless fixture requires non-root test process")
    def test_changed_imported_manifest_stops_before_podman(self):
        code,calls=self.execute("BAD")
        self.assertNotEqual(code,0)
        self.assertIn("skopeo inspect --raw containers-storage:",calls)
        self.assertNotIn("podman",calls)
