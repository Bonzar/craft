"""Verify a reviewed QA artifact, extract safely, and verify every release payload file."""
import hashlib
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

source, dest, expected_sha, revision = sys.argv[1:]
if not re.fullmatch(r'[0-9a-f]{64}', expected_sha) or not re.fullmatch(r'[0-9a-f]{40}', revision):
    raise SystemExit('Expected lowercase SHA256 and exact Git revision')
with open(source, 'rb') as stream:
    if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_sha:
        raise SystemExit('Artifact digest mismatch')
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode='r:gz') as archive:
        seen = set()
        total = 0
        for member in archive.getmembers():
            p = PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or p in seen:
                raise SystemExit('Unsafe or duplicate archive path')
            if not (member.isfile() or member.isdir()):
                raise SystemExit('Unsupported archive item')
            if member.size > 100_000_000:
                raise SystemExit('Archive member too large')
            total += member.size
            if total > 250_000_000 or len(seen) > 8000:
                raise SystemExit('QA archive exceeds bounds')
            if p.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.key', '.pem', '.env'} or p.name == '.env':
                raise SystemExit('QA archive must not contain DBs or secrets')
            seen.add(p)
        archive.extractall(dest, filter='data')
root = Path(dest)
if (root/'QA_PROFILE').read_text() != 'assertion-memory-synthetic-qa-v1\n':
    raise SystemExit('Wrong release profile')
if (root/'REVISION').read_text().strip() != revision:
    raise SystemExit('Wrong Git SHA')
manifest = root/'SHA256SUMS'
expected = {}
for line in manifest.read_text().splitlines():
    h, separator, path = line.partition('  ')
    p = PurePosixPath(path)
    if not separator or not re.fullmatch('[a-f0-9]{64}',h) or p.is_absolute() or '..' in p.parts or p in expected:
        raise SystemExit('Invalid SHA256SUMS')
    expected[p] = h
actual = {PurePosixPath(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p != manifest}
if actual != set(expected):
    raise SystemExit('Archive/manifest mismatch')
for p,h in expected.items():
    with (root/p).open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest() != h:
            raise SystemExit('Bad payload digest')
print('Reviewed release digest, version, and payload manifest verified.')
