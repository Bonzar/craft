"""Fixed TEST Nginx operator action. Does not grant or install privilege."""
from argparse import ArgumentParser
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from render_test import nginx,PRIVATE_PATH

ROUTES=Path('/etc/nginx/assertion-visual-test-edge/routes.conf')
LOCK=Path('/run/assertion-visual-test-ingress.lock')
MAX=128*1024
PIN=re.compile(r'[a-f0-9]{64}\Z')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def trusted_dir(path):
    st=path.lstat()
    if path.resolve(strict=True)!=path or not stat.S_ISDIR(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:
        raise ValueError('unsafe fixed TEST config directory')

def read_source(path):
    trusted_dir(path.parent)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_nlink!=1 or st.st_mode&0o022
            or st.st_size>MAX or not os.path.samestat(st,path.lstat())):
            raise ValueError('unsafe fixed TEST config')
        data=f.read(MAX+1)
    if len(data)>MAX:
        raise ValueError('oversize TEST configuration')
    return data,st

def replace(path,data,metadata):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.test-events-')
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchown(f.fileno(),metadata.st_uid,metadata.st_gid)
            os.fchmod(f.fileno(),stat.S_IMODE(metadata.st_mode))
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.lexists(tmp): os.unlink(tmp)

@contextmanager
def scoped_lock():
    trusted_dir(LOCK.parent)
    fd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,0o600)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_nlink!=1
            or stat.S_IMODE(st.st_mode)!=0o600 or not os.path.samestat(st,LOCK.lstat())):
            raise ValueError('unsafe TEST config lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)

def services(command):
    # Output can contain site configuration. Do not forward it to logs.
    cmd={'validate':['/usr/sbin/nginx','-t','-q'],
         'reload':['/usr/bin/systemctl','reload','nginx.service']}[command]
    subprocess.run(cmd,check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL,cwd='/',env={'PATH':'/usr/sbin:/usr/bin'},timeout=12)

def compute(before,reverse=False):
    text=before.decode('utf-8')
    if not reverse:
        return nginx(text).encode('utf-8')
    head=list(re.finditer(r'^([ \t]*)location = '+re.escape(PRIVATE_PATH)+r' \{\n',text,re.M))
    if len(head)!=1:
        raise ValueError('expected one exact installed TEST route')
    start=head[0]
    end=re.search(r'^'+re.escape(start[1])+r'\}\n',text[start.end():],re.M)
    if end is None:
        raise ValueError('installed TEST route has no end')
    old=text[:start.start()]+text[start.end()+end.end():]
    if nginx(old)!=text:
        raise ValueError('installed TEST route differs from reviewed insertion')
    return old.encode('utf-8')

def exact_change(before,wanted,prior_pin,next_pin,read,write,verify):
    if (not PIN.fullmatch(prior_pin) or not PIN.fullmatch(next_pin)
        or digest(before)!=prior_pin or digest(wanted)!=next_pin or read()!=before or before==wanted):
        raise ValueError('reviewed digests or current config drift')
    try:
        write(wanted)
        verify('validate')
        verify('reload')
    except BaseException:
        # Restore only if this command still owns the exact current candidate.
        current=read()
        if current==wanted:
            write(before)
            verify('validate')
            verify('reload')
        elif current!=before:
            raise RuntimeError('TEST config externally changed during rollback')
        raise

def main():
    p=ArgumentParser()
    p.add_argument('mode',choices=('plan','apply','plan-remove','remove'))
    p.add_argument('--direct-page-pair',type=Path)
    p.add_argument('--direct-origin')
    p.add_argument('--page-sha256')
    p.add_argument('--before')
    p.add_argument('--after')
    a=p.parse_args()
    if os.geteuid()!=0:
        raise SystemExit('fixed operator command requires configuration ownership')
    reverse=a.mode.endswith('remove')
    if a.direct_page_pair:
        if not a.direct_origin:
            raise ValueError('exact TEST direct origin is mandatory')
        from direct_test_bundle import render_routes,validate_page
        from render_direct_test import PROOF_FILE
        pair=json.loads(read_source(a.direct_page_pair)[0])
        if set(pair)!= {'html','policy'}:
            raise ValueError('invalid proof bundle')
        actual_page_hash=validate_page(pair['html'],pair['policy'])
        def wanted_for(before):
            return render_routes(before.decode('utf-8'),pair['policy'],a.direct_origin,reverse).encode()
        def match_installed_page(required=True):
            try:
                page,metadata=read_source(Path(PROOF_FILE))
            except FileNotFoundError:
                if required:
                    raise
                return None
            if page!=pair['html'].encode():
                raise ValueError('installed TEST proof file differs')
            return metadata
    else:
        if a.direct_origin or a.page_sha256:
            raise ValueError('direct proof options require the explicit bundle')
        def wanted_for(before):
            return compute(before,reverse)
    if a.mode.startswith('plan'):
        before,_=read_source(ROUTES)
        wanted=wanted_for(before)
        if a.before or a.after or a.page_sha256:
            raise ValueError('plan never accepts caller inputs')
        print('test_nginx_before_sha256='+digest(before))
        print('test_nginx_after_sha256='+digest(wanted))
        if a.direct_page_pair:
            print('test_proof_html_sha256='+actual_page_hash)
        return
    if not a.before or not a.after:
        raise ValueError('both reviewed digest arguments are mandatory')
    with scoped_lock():
        before,st=read_source(ROUTES)
        wanted=wanted_for(before)
        if a.direct_page_pair:
            if a.page_sha256!=actual_page_hash:
                raise ValueError('reviewed proof page digest must match')
            # Stage the fixed file separately (root-owned, 0644) before adding
            # any serving route. Neither apply nor undo overwrites that file.
            metadata=match_installed_page()
            if stat.S_IMODE(metadata.st_mode)!=0o644:
                raise ValueError('expected readable, root-owned proof page')
        exact_change(before,wanted,a.before,a.after,lambda:read_source(ROUTES)[0],
                     lambda data:replace(ROUTES,data,st),services)
    print('test_nginx_validated_and_reloaded')

if __name__=='__main__':
    main()
