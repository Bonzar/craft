"""Visual restricted entrypoint: check authority, call shared deploy, return receipt."""
import json
import os
from pathlib import Path
import pwd
import sys
if sys.flags.isolated:
    product=Path('/etc/assertion-memory-visual') / (sys.argv[1] if len(sys.argv)==2 and sys.argv[1] in ('test','prod') else 'invalid')
    shared=Path('/etc/assertion-release/shared')
    if Path(__file__) != product/'visual_receive.py':
        raise SystemExit('invalid fixed visual receiver path')
    for path in (product, shared):
        st=path.stat()
        if path.is_symlink() or not path.is_dir() or st.st_uid!=0 or st.st_mode&0o022:
            raise SystemExit('untrusted installed receiver directory')
    sys.path[:0]=[str(shared),str(product)]

from visual_auth import COMMAND, verify_frame
from visual_policy import deploy_verified

ROOT=Path('/var/lib/assertion-memory-visual')
CONFIG=Path('/etc/assertion-memory-visual')


def run(mode,command,stream):
    match=COMMAND.fullmatch(command)
    if not match or match[1]!=mode or mode not in ('test','prod'):
        return {'status':'rejected','error':'command_denied'},1
    if pwd.getpwuid(os.getuid()).pw_name!='assertion-visual-'+mode+'-deploy':
        return {'status':'rejected','error':'unexpected_receiver_identity'},1
    signers=CONFIG/mode/'allowed-signers'
    stat=signers.stat()
    if signers.is_symlink() or not signers.is_file() or stat.st_uid!=0 or stat.st_mode&0o022:
        return {'status':'rejected','error':'untrusted_signer_policy'},1
    verify_frame(signers,command,stream)
    digest=match[3]
    release=deploy_verified(stream,digest,root=ROOT/mode)
    return {'environment':mode,'revision':match[2],'archive_sha256':digest,'release':release},0


def main():
    try:
        mode,=sys.argv[1:]
        reply,status=run(mode,os.environ.get('SSH_ORIGINAL_COMMAND',''),sys.stdin.buffer)
    except Exception:
        reply,status={'status':'rejected','error':'visual_deployment_failed'},1
    print(json.dumps(reply,separators=(',',':')))
    return status


if __name__=='__main__':
    raise SystemExit(main())
