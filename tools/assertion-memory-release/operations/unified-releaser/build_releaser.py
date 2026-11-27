"""Create reproducible release asset from the review-only source bundle."""
import gzip
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tarfile

BASE=Path(__file__).resolve().parent
MEMBERS=('README.md','build_releaser.py','releaser.py','test_releaser.py','workflow.yml.review')


def asset():
    files={name:(BASE/name).read_bytes() for name in MEMBERS}
    files['SHA256SUMS']=''.join(f'{sha256(raw).hexdigest()}  {name}\n'
        for name,raw in sorted(files.items())).encode('ascii')
    compressed=BytesIO()
    with gzip.GzipFile(fileobj=compressed,filename='',mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w',format=tarfile.USTAR_FORMAT) as t:
            for name,raw in sorted(files.items()):
                member=tarfile.TarInfo(name)
                member.mode=0o644
                member.size=len(raw)
                t.addfile(member,BytesIO(raw))
    return compressed.getvalue()


if __name__=='__main__':
    blob=asset()
    out=Path('assertion-releaser.tar.gz')
    with out.open('xb') as stream:
        stream.write(blob)
    print(sha256(blob).hexdigest())
