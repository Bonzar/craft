"""Offline tests of the TEST wheel lock grammar (never run pip or the deploy hook)."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

HOOK = Path(__file__).with_name("test-service-hook.sh")
SHA = "a" * 64


class TestOfflinePrepare(unittest.TestCase):
    def test_syntax(self):
        subprocess.run(["bash", "-n", HOOK], check=True)

    def test_pin_grammar(self):
        script = HOOK.read_text().split("<<'PY_LOCK'\n", 1)[1].split("\nPY_LOCK", 1)[0]
        pin = f"uvicorn==0.5 --hash=sha256:{SHA}"
        for contents, accepted in (
            (f"{pin}\n", True),
            (f"{pin} --hash=sha256:{SHA}\nh11==1.0 --hash=sha256:{SHA}\n", True),
            ("", False), ("--extra-index-url=https://untrusted.invalid\n", False),
            (f"uvicorn @ https://untrusted.invalid/file.whl --hash=sha256:{SHA}\n", False),
            ("uvicorn==0.5\n", False), (f"{pin}\n-r alternate.txt\n", False),
        ):
            with self.subTest(accepted=accepted, contents=contents), TemporaryDirectory() as directory:
                lock = Path(directory) / "requirements.txt"
                lock.write_text(contents)
                result = subprocess.run([sys.executable, "-I", "-B", "-c", script, lock],
                                        capture_output=True, check=False)
                self.assertEqual(result.returncode == 0, accepted)


if __name__ == "__main__":
    unittest.main()
