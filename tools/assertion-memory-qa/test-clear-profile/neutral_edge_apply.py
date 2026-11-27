"""Fixed existing TEST alias operator. Plan is read-only; no rights are granted."""
from argparse import ArgumentParser
from contextlib import ExitStack,contextmanager
import fcntl,json,os,pwd,stat,subprocess,tempfile
from pathlib import Path

from install_profile import activation_lock,parse,regular_bytes
from neutral_edge_plan import PIN,derive,digest,inverse

TARGET=Path('/etc/nginx/conf.d/assertion-memory-probe-tls.conf')
LOCK=Path('/run/assertion-visual-test-ingress.lock')
REGISTRY=Path('/etc/assertion-release/test/private-operations.json')
KEY=Path('/etc/assertion-release/test/qa-runner-machine.key')
MAX_LOADED=2*1024*1024

def trusted_dir(path):
    st=path.lstat()
    if (path.resolve(strict=True)!=path or not stat.S_ISDIR(st.st_mode)
            or st.st_uid!=0 or st.st_mode&0o022):
        raise ValueError('Untrusted fixed TEST ingress location')

def read_target():
    trusted_dir(TARGET.parent)
    s=TARGET.lstat()
    value=regular_bytes(TARGET,maximum=128*1024,uid=0)
    if not os.path.samestat(s,TARGET.lstat()):
        raise ValueError('TEST source changed')
    return value,s

def empty_machine_registry():
    raw=regular_bytes(REGISTRY,maximum=12000,uid=0)
    body=parse(raw)
    if type(body) is not dict or type(body.get('machine_clients',{})) is not dict or body.get('machine_clients',{}):
        raise ValueError('TEST machine authority is not empty')
    try:KEY.lstat()
    except FileNotFoundError:pass
    else:raise ValueError('Existing TEST runner credential; do not reopen or shift public reachability')
    return 0

@contextmanager
def scoped_lock():
    # Existing approved TEST ingress lock; requires root's serial shared-daemon
    # coordination with any separate PROD/Gateway work before the caller starts.
    trusted_dir(LOCK.parent)
    fd=os.open(LOCK,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        s=os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_nlink!=1
                 or stat.S_IMODE(s.st_mode)!=0o600 or not os.path.samestat(s,LOCK.lstat())):
            raise ValueError('Unverified existing TEST ingress lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)

def process(args,**kwargs):
    return subprocess.run(args,stdin=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd='/',
        env={'PATH':'/usr/sbin:/usr/bin','LC_ALL':'C'},timeout=15,check=True,**kwargs)

def loaded():
    result=process(['/usr/sbin/nginx','-T'],stdout=subprocess.PIPE).stdout
    if not 0<len(result)<=MAX_LOADED:raise ValueError('Unbounded Nginx config dump')
    return result

def validate():
    process(['/usr/sbin/nginx','-t','-q'],stdout=subprocess.DEVNULL)

def reload():
    process(['/usr/bin/systemctl','reload','nginx.service'],stdout=subprocess.DEVNULL)

def replace(body, metadata):
    fd,tmp=tempfile.mkstemp(dir=TARGET.parent,prefix='.test-neutral-')
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchown(f.fileno(),metadata.st_uid,metadata.st_gid)
            os.fchmod(f.fileno(),stat.S_IMODE(metadata.st_mode))
            f.write(body);f.flush();os.fsync(f.fileno())
        os.replace(tmp,TARGET)
        dirfd=os.open(TARGET.parent,os.O_DIRECTORY|os.O_NOFOLLOW|os.O_RDONLY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    finally:
        if os.path.lexists(tmp):os.unlink(tmp)

def expected_loaded(before,candidate,snapshot):
    # Prove the files of every other server and location remained byte-for-byte.
    # Never output the dump, which may contain unrelated service information.
    head=b'# configuration file '+str(TARGET).encode('ascii')+b':\n'
    if snapshot.count(head)!=1 or snapshot.count(head+before)!=1:
        raise ValueError('Pinned TEST source not uniquely represented in loaded Nginx')
    return snapshot.replace(head+before,head+candidate,1)

def exact_change(before,wanted,original,expected,read,write,read_loaded,verify_service,reload_service):
    if read()!=before or read_loaded()!=original or before==wanted:
        raise ValueError('Live TEST edge changed')
    try:
        write(wanted)
        if read()!=wanted or read_loaded()!=expected:
            raise ValueError('Unexpected TEST or unrelated Nginx bytes changed')
        verify_service()
        if read()!=wanted or read_loaded()!=expected:
            raise ValueError('Full edge changed after validation; do not reload')
        reload_service()
    except BaseException:
        present=read()
        if present==wanted:
            write(before)
            # Do not reload over someone else's new concurrent configuration.
            if read_loaded()!=original:
                raise RuntimeError('Unrelated ingress changed; TEST file restored, no blind reload')
            verify_service()
            if read()!=before or read_loaded()!=original:
                raise RuntimeError('Unrelated edge changed after rollback validation; do not reload')
            reload_service()
        elif present!=before:
            raise RuntimeError('Scoped TEST ingress changed externally; do not overwrite')
        raise

def main():
    p=ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('plan','apply','plan-restore'))
    p.add_argument('--baseline-file-sha256',required=True)
    p.add_argument('--loaded-sha256',required=True)
    p.add_argument('--candidate-file-sha256')
    args=p.parse_args()
    if os.getuid()!=0 or os.geteuid()!=0:
        raise SystemExit('Existing protected TEST operator authority required')
    try:
        reversing='restore' in args.mode
        writing=not args.mode.startswith('plan')
        with ExitStack() as stack:
            if writing:
                uid=pwd.getpwnam('assertion-test-deploy').pw_uid
                if uid<=0:raise ValueError('Wrong scoped TEST release authority')
                # Same lock as runner enrollment, before the existing TEST
                # ingress lock. No standalone restore can reopen this route.
                stack.enter_context(activation_lock(uid))
                stack.enter_context(scoped_lock())
            before,metadata=read_target()
            original=loaded()
            if (not PIN.fullmatch(args.loaded_sha256) or digest(original)!=args.loaded_sha256
                    or empty_machine_registry()!=0):
                raise ValueError('Reviewed live full edge or empty authority proof changed')
            if reversing:
                wanted=inverse(before,expected_after_sha256=args.candidate_file_sha256,
                               expected_before_sha256=args.baseline_file_sha256)
            else:
                # Alternate forwards and Gateway paths are independently verified
                # in the exact original inventory, not inferred from this flag.
                wanted=derive(before,expected_sha256=args.baseline_file_sha256,
                    registered_clients=0,gateway_paths_absent=True,alternate_forwards_absent=True)['candidate']
                if writing and digest(wanted)!=args.candidate_file_sha256:
                    raise ValueError('Candidate does not match reviewed isolated TEST diff')
            after=expected_loaded(before,wanted,original)
            if writing:
                exact_change(before,wanted,original,after,lambda:read_target()[0],
                             lambda b:replace(b,metadata),loaded,validate,reload)
            print(json.dumps(dict(stage='test',only_target='/memory-api-test',before_file_sha256=digest(before),
               after_file_sha256=digest(wanted),expected_loaded_after_sha256=digest(after),writes_performed=writing,
               reload_requested=writing,public_route_verification_pending=writing),sort_keys=True))
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,subprocess.SubprocessError):
        raise SystemExit('Scoped TEST edge operation unconfirmed; read back central state before retry') from None

if __name__=='__main__':main()
