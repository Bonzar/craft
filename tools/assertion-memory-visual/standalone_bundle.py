"""Deterministic single Visual release package; pure candidate format, not deployed."""
from io import BytesIO
import zipfile

if __package__:
    from . import runtime_companion as runtime
else:
    import runtime_companion as runtime

SCHEMA='visual-standalone-release-v1'
MAX_STATIC=5_000_000
MAX_TOTAL=MAX_STATIC+runtime.MAX_ZIP+8192
FILES={'visual.tar':MAX_STATIC,'runtime.zip':runtime.MAX_ZIP,'release.json':4096}


def receipt(expected, static, companion):
    runtime.identity(**expected)
    if type(static) is not bytes or not 0<len(static)<=MAX_STATIC or runtime.digest(static)!=expected['static_sha']:
        raise ValueError('Combined Visual static bytes mismatch')
    companion_sha=runtime.digest(companion)
    runtime.verify(companion,expected,companion_sha)
    return runtime.wire({'schema':SCHEMA,'identity':expected,'runtime_sha256':companion_sha})


def build(expected, static, companion):
    contents={'visual.tar':static,'runtime.zip':companion,'release.json':receipt(expected,static,companion)}
    out=BytesIO()
    with zipfile.ZipFile(out,'w') as zip:
        for name,data in sorted(contents.items()):
            info=zipfile.ZipInfo(name)
            info.create_system=3
            info.external_attr=0o100644<<16
            zip.writestr(info,data)
    return out.getvalue()


def verify(raw,expected,outer_sha):
    """Caller first checks immutable provider run/artifact and pinned digest."""
    if (type(raw) is not bytes or not 0<len(raw)<=MAX_TOTAL or not isinstance(outer_sha,str)
            or not runtime.DIGEST.fullmatch(outer_sha) or runtime.digest(raw)!=outer_sha):
        raise ValueError('Wrong combined Visual archive')
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            infos=z.infolist()
            if (len(infos)!=len(FILES) or {i.filename for i in infos}!=set(FILES)
                    or any(i.is_dir() or not 0<i.file_size<=FILES[i.filename] or i.external_attr>>16!=0o100644
                           or i.compress_type!=zipfile.ZIP_STORED or i.flag_bits&1 for i in infos)):
                raise ValueError('Wrong combined Visual archive members')
            static,companion,doc=(z.read(x) for x in ('visual.tar','runtime.zip','release.json'))
    except (zipfile.BadZipFile,KeyError,RuntimeError) as e:
        raise ValueError('Invalid combined Visual archive') from e
    if doc!=receipt(expected,static,companion):
        raise ValueError('Wrong combined Visual release binding')
    return static,companion


def artifact_name(release_id, revision):
    if not runtime.ID.fullmatch(release_id) or not runtime.SHA.fullmatch(revision):
        raise ValueError('Invalid packed Visual artifact selection')
    return f'assertion-memory-visual-packed-{release_id}-{revision}'


def open_original(raw, original, outer_sha):
    # Extract the static digest to build the expected inner descriptor, but trust
    # nothing in the archive until verify() independently qualifies all bytes.
    if type(raw) is not bytes or not 0<len(raw)<=MAX_TOTAL or runtime.digest(raw)!=outer_sha:
        raise ValueError('Wrong packed original')
    with zipfile.ZipFile(BytesIO(raw)) as z:
        info=z.getinfo('visual.tar')
        if not 0<info.file_size<=MAX_STATIC or info.compress_type!=zipfile.ZIP_STORED:
            raise ValueError('Invalid packed Visual static header')
        static_sha=runtime.digest(z.read(info))
    exp=runtime.identity(original.tag,original.revision,original.release_id,original.build_run_id,static_sha)
    return exp,verify(raw,exp,outer_sha)


def main():
    import argparse
    import json
    from pathlib import Path
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('tag','revision','release-id','build-run-id'):
        p.add_argument('--'+name,required=True)
    for name in ('static','runtime','out','static-receipt'):
        p.add_argument('--'+name,type=Path,required=True)
    x=p.parse_args()
    static=x.static.read_bytes()
    sha=json.loads(x.static_receipt.read_text())['archive_sha256']
    exp=runtime.identity(x.tag,x.revision,x.release_id,x.build_run_id,sha)
    raw=build(exp,static,x.runtime.read_bytes())
    x.out.mkdir(mode=0o700)
    (x.out/'visual-release.zip').write_bytes(raw)


if __name__=='__main__':
    main()
