"""Run shared assembly qualification against the visual builder's exact output."""
import json
from pathlib import Path
import sys

from assembly import inspect
from visual_policy import archive_spec, validate


def main():
    archive, receipt = map(Path,sys.argv[1:])
    model=json.loads(receipt.read_bytes())
    release=inspect(archive, model['archive_sha256'], spec=archive_spec(),validate=validate)
    if release!=model['release'] or model['bytes']!=archive.stat().st_size:
        raise ValueError('visual_build_assembly_proof_mismatch')


if __name__=='__main__':
    main()
