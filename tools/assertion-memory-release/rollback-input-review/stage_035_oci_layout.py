"""Stage full pinned OCI layout from verified digest-named blobs; no runtime."""
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from compose_035_oci_descriptor import COMPOSED, compose, encode
from verify_035_oci_root import PIN, unique


def copy_exact(source, dest, digest, size):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ValueError("Invalid staged base layer size")
        with dest.open("xb") as target:
            shutil.copyfileobj(f, target, 1024 * 1024)
    with dest.open("rb") as f:
        if dest.stat().st_size != size or file_digest(f, "sha256").hexdigest() != digest:
            raise ValueError("Base layer changed while staging")


def stage(base, layer, output, pin):
    objects, index, report = compose(base, layer, pin)
    md = report["manifest"]["digest"]
    manifest = json.loads(objects[md])
    output.parent.resolve(strict=True)
    if output.exists() or output.is_symlink():
        raise ValueError("Layout destination must be absent")
    with tempfile.TemporaryDirectory(prefix=".oci-review-", dir=output.parent) as temp:
        ready = Path(temp) / "layout"
        blobs = ready / "blobs" / "sha256"
        blobs.mkdir(parents=True, mode=0o700)
        for digest, data in objects.items():
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("Invalid OCI digest")
            with (blobs / digest[7:]).open("xb") as f:
                f.write(data)
            with (blobs / digest[7:]).open("rb") as f:
                if file_digest(f, "sha256").hexdigest() != digest[7:]:
                    raise ValueError("Generated OCI blob changed")
        for d in manifest["layers"]:
            digest = d["digest"]
            if digest not in objects:
                if digest not in pin["layers"]:
                    raise ValueError("Unexpected OCI base layer")
                copy_exact(base / digest[7:], blobs / digest[7:], digest[7:], d["size"])
        (ready / "index.json").write_bytes(index)
        (ready / "oci-layout").write_bytes(encode({"imageLayoutVersion": "1.0.0"}))
        ready.rename(output)
    return report


def check_repo_digest(requested, observed):
    """Exact local RepoDigest only, never config ID, index digest or mutable tag."""
    digest = COMPOSED["manifest"][0]
    if (type(requested) is not str or not re.fullmatch(
            r"[a-z0-9][a-z0-9./:_-]*@" + re.escape(digest), requested)
            or requested not in observed.splitlines()):
        raise ValueError("Offline importer did not preserve exact composed manifest RepoDigest")


if __name__ == "__main__":
    import argparse
    import sys
    p = argparse.ArgumentParser(description=__doc__)
    command = p.add_subparsers(dest="op", required=True)
    layout = command.add_parser("stage")
    for n in ("base", "layer", "output"):
        layout.add_argument(n, type=Path)
    check = command.add_parser("check-repo-digest")
    check.add_argument("requested")
    args = p.parse_args()
    if args.op == "stage":
        print(json.dumps(stage(args.base, args.layer, args.output,
                               json.loads(PIN.read_bytes(), object_pairs_hook=unique)), sort_keys=True))
    else:
        check_repo_digest(args.requested, sys.stdin.read())
