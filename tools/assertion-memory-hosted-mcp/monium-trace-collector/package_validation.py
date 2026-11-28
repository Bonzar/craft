"""Pure package preflight; no authority, writes, process or credential access."""
from hashlib import sha256
from pathlib import PurePosixPath as P
from re import fullmatch
FOLDER=P('tools/assertion-memory-hosted-mcp/monium-trace-collector')
PINS={
 'common.yaml':'bba5c6e6a4f7f1b61870bb50db0cc9d239ee66dcaa9d93a97255219c557a1b25',
 'test.yaml':'914dbbf28ca2980621b872aaecfe19a65151cbc93ced97eca092ef68903c6684',
 'prod.yaml':'22f7c37d1104026258a1ef9a0b8e72f58ff1e76ca911ba215c7d62d1398f82f9',
 'assertion-monium-traces@.service':'4446cb33b08db0c6b951aa8c577b83bfc1daa410ee10f266f1f41010e866881c',
}
def qualify(*, lane, reviewed_revision, actual_revision, authorized_archive_sha, verified_archive_sha, manifest, assets):
    if (lane!='test' or type(reviewed_revision) is not str or not fullmatch('[a-f0-9]{40}',reviewed_revision)
        or actual_revision!=reviewed_revision or type(authorized_archive_sha) is not str
        or not fullmatch('[a-f0-9]{64}',authorized_archive_sha) or verified_archive_sha!=authorized_archive_sha):
        raise ValueError('trusted exact TEST package required')
    if type(manifest) is not dict or type(assets) is not dict or set(assets)!={FOLDER/n for n in PINS}:
        raise ValueError('expected exact approved non-secret asset namespace')
    for file,digest in PINS.items():
        p=FOLDER/file
        b=assets[p]
        if manifest.get(p)!=digest or type(b) is not bytes or not 0<len(b)<=65536 or sha256(b).hexdigest()!=digest:
            raise ValueError('asset or manifest digest mismatch')
    # Package verification does not copy or return PROD config, templates, or credentials.
    return (FOLDER/'common.yaml', PINS['common.yaml'])
