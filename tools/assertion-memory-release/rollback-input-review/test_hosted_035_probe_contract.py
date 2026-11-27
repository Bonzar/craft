"""Check exact synthetic actuator invocation with a fake Docker executable."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from compose_035_oci_descriptor import COMPOSED


class ProbeContract(unittest.TestCase):
    def test_actuator_carries_only_fixed_isolation_arguments(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            docker = root/"docker"
            docker.write_text("""#!/bin/bash
set -euo pipefail
if [[ "$1" == image ]]; then
  printf "%s\\n" "${@: -1}"
else
  printf "%s\\0" "$@" > "$LOG"
fi
""")
            docker.chmod(0o700)
            control = Path(__file__).resolve().parent
            image = "127.0.0.1:4444/rollback-base035@" + COMPOSED["manifest"][0]
            log = root/"args"
            subprocess.run(["bash",str(control/"hosted_035_synthetic_probe.sh"),image,str(control)],
                           env=os.environ|{"PATH":f"{root}:{os.environ['PATH']}","LOG":str(log)},
                           check=True, capture_output=True)
            argv = log.read_bytes().decode().split("\0")
            for expected in ("unix:///var/run/docker.sock", "--pull=never", "--network=none",
                             "--read-only", "65534:65534", "--cap-drop=ALL",
                             "--security-opt=no-new-privileges", image):
                self.assertIn(expected, argv)
            for forbidden in ("--privileged", "--volume", "-v", "--mount", "--env"):
                self.assertNotIn(forbidden, argv)
