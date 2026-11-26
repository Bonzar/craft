#!/usr/bin/env python3
"""Migrate one reviewed Base PROD smoke hook to the Visual owner password policy.

This changes the local smoke precondition only. It does not change passwords,
the machine-token policy, authentication, or the protected GitHub workflow.
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import tempfile

TARGET = Path("/etc/assertion-release/production/prod_service_hook.py")
OLD_SHA = "539215d0b24c6aa0e7bcd05c1e08d0f507f20a1f0cf289f214a5cef3501c6ad7"
OLD = b"len(graph_password)>=24"
NEW = b"len(graph_password)>=12"


def candidate(source):
    if hashlib.sha256(source).hexdigest() != OLD_SHA or source.count(OLD) != 1:
        raise ValueError("Unexpected installed production smoke hook")
    changed = source.replace(OLD, NEW, 1)
    compile(changed, str(TARGET), "exec")
    return changed


def read_hook():
    with os.fdopen(os.open(TARGET, os.O_RDONLY | os.O_NOFOLLOW), "rb") as f:
        info = os.fstat(f.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o644 or info.st_size > 128_000):
            raise ValueError("Unexpected hook metadata")
        return f.read(128_001), info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="install as a privileged host operator")
    args = parser.parse_args()
    before, info = read_hook()
    after = candidate(before)
    if args.apply:
        if os.geteuid() != 0:
            raise ValueError("Only a host administrator may install the smoke hook")
        fd, name = tempfile.mkstemp(prefix=".prod-smoke-", dir=TARGET.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                os.fchown(f.fileno(), info.st_uid, info.st_gid)
                os.fchmod(f.fileno(), 0o644)
                f.write(after)
                f.flush()
                os.fsync(f.fileno())
            current, live = read_hook()
            if current != before or (live.st_dev, live.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Hook changed before install")
            os.replace(name, TARGET)
            dir_fd = os.open(TARGET.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            if read_hook()[0] != after:
                raise ValueError("Installed hook failed readback")
        finally:
            if os.path.exists(name):
                os.unlink(name)
    print(("Installed" if args.apply else "Validated"), "smoke hook SHA256", hashlib.sha256(after).hexdigest())


if __name__ == "__main__":
    main()
