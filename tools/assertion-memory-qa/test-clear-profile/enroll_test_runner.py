"""One reviewed existing-host TEST runner enrollment. Default is read-only.

No route, account, service definition, PROD file or database is modified.
Only the root operator can install a new runner key or change its fingerprint.
The raw credential never leaves the fixed owner-only runner file.
"""
import argparse
from hashlib import sha256
import json, os, re, secrets, stat, subprocess
from pathlib import Path
import pwd

from install_profile import (RELEASE,JOURNAL,activation_lock,parse,regular_bytes,verify_installed_sources,verify_graph)
from profile_plan import compact,release_generation
from witness_one_test_batch import (credential,discover,loopback,installed_client,
    DIR,REGISTRY,KEY,CLIENT,REV,ARCHIVE,TAG,GENERATION)

UNIT='assertion-test-deploy-mcp.service'
MANIFEST='a713d6e6669f1425a09d3cce6a5db6a2cf1917856017dff601d43d0e88ad316f'
HEX=re.compile(r'[0-9a-f]{64}\Z')
EXPECTED_PATHS={
 'read_token_file':str(RELEASE/'state'/'private-operations-read-token'),
 'effect_token_file':str(RELEASE/'state'/'private-operations-effects-token'),
}

def absent(path):
    try:path.lstat()
    except FileNotFoundError:return True
    return False

def guard_root():
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('Existing protected TEST operator authority required')

def trusted_dir():
    for p in (DIR,DIR.parent):
        st=p.lstat()
        if p.resolve(strict=True)!=p or st.st_uid or st.st_mode&0o022 or not stat.S_ISDIR(st.st_mode):
            raise ValueError('Untrusted TEST operator directory')

def existing():
    raw=regular_bytes(REGISTRY,maximum=12000,uid=0)
    st=REGISTRY.lstat()
    # Preserve exact stage profile, only add our one machine digest.
    data=parse(raw)
    base={'capabilities','read_token_file','effect_token_file'}
    if type(data) is not dict or set(data) not in (base,base|{'machine_clients'}):
        raise ValueError('Unknown TEST machine profile')
    caps=data['capabilities']
    if (caps not in (['baseline_read'],['baseline_read','intake_process'],['baseline_read','intake_process','generate'])
            or data['read_token_file']!=EXPECTED_PATHS['read_token_file']
            or data['effect_token_file']!=(None if caps==['baseline_read'] else EXPECTED_PATHS['effect_token_file'])):
        raise ValueError('Wrong TEST-only inputs')
    registry=data.get('machine_clients',{})
    if type(registry) is not dict or set(registry)-{CLIENT}:
        raise ValueError('Unexpected existing TEST machines; do not touch')
    return raw,st,data,registry

def enroll_payload(doc,registry,fingerprint):
    if registry or not HEX.fullmatch(fingerprint):
        raise ValueError('Expected exactly new TEST runner')
    return compact(doc|{'machine_clients':{CLIENT:fingerprint}})+b'\n'

def ensure_current(uid):
    if not absent(JOURNAL):
        raise ValueError('TEST activation journal present')
    current=(RELEASE/'current').resolve(strict=True)
    expected=RELEASE/'releases'/(REV+'-'+ARCHIVE)
    if current!=expected:
        raise ValueError('Protected TEST current release changed')
    src=verify_installed_sources(current,MANIFEST)
    if src['REVISION'].strip().decode()!=REV or src['BASE_RELEASE_TAG'].strip().decode()!=TAG:
        raise ValueError('Wrong TEST package tag or source')
    prefix=verify_graph(uid,current)
    out=subprocess.run([*prefix,'show',UNIT,'-p','ActiveState','-p','MainPID'],
        capture_output=True,check=True,timeout=8,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'}).stdout.decode('ascii')
    fields=dict(line.split('=',1) for line in out.splitlines() if '=' in line)
    if fields.get('ActiveState')!='active' or not re.fullmatch('[1-9][0-9]{0,9}',fields.get('MainPID','')):
        raise ValueError('Canonical TEST Base service is not healthy')
    proc=Path('/proc')/fields['MainPID']
    if proc.stat().st_uid!=uid or proc.joinpath('cwd').resolve(strict=True)!=current:
        raise ValueError('Wrong physical TEST Base writer')
    argv=proc.joinpath('cmdline').read_bytes().split(b'\x00')[:-1]
    # Config is selected by known protected installed Base service, not caller.
    cfg=[Path(x.decode('ascii')) for x in argv if x.endswith(b'.json')]
    if len(cfg)!=1 or cfg[0].parent!=RELEASE/'state' or cfg[0].resolve(strict=True)!=cfg[0]:
        raise ValueError('TEST Base config path unverified')
    config=parse(regular_bytes(cfg[0],maximum=12000,uid=uid))
    if (config.get('port')!=8893 or config.get('public_host')!='localhost'
            or config.get('service',{}).get('db')!=str(RELEASE/'state'/'fictional.sqlite3')):
        raise ValueError('Wrong TEST Base source or private listener')
    return prefix

def assign_root_file(path,body,mode,gid, *, absent_only=False, prior=None):
    if (path==KEY and (not absent_only or prior is not None or mode!=0o600 or gid!=0)
            or path==REGISTRY and (absent_only or prior is None) or path not in (KEY,REGISTRY)):
        raise ValueError('Only the exact authorized TEST runner files are writable')
    fd=os.open(DIR,os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY|os.O_CLOEXEC)
    name='.approved-test-qa-'+secrets.token_hex(12)
    made=False
    try:
        tmpfd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        made=True
        with os.fdopen(tmpfd,'wb') as f:
            f.write(body)
            f.flush()
            os.fchown(f.fileno(),0,gid)
            os.fchmod(f.fileno(),mode)
            os.fsync(f.fileno())
        if absent_only:
            os.link(name,path.name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
        else:
            raw,st=prior
            if (regular_bytes(path,maximum=12000,uid=0)!=raw
                    or not os.path.samestat(st,path.lstat())):
                raise ValueError('TEST registry drift; do not write')
            os.replace(name,path.name,src_dir_fd=fd,dst_dir_fd=fd)
            made=False
    finally:
        try:
            if made:os.unlink(name,dir_fd=fd)
            os.fsync(fd)
        finally:os.close(fd)

def inspect():
    guard_root(); trusted_dir()
    raw,st,doc,registry=existing()
    return dict(stage='test',registry_sha256=sha256(raw).hexdigest(),
       has_qa_runner=CLIENT in registry,has_local_key=not absent(KEY),release=TAG,writes=False)

def enroll(expected_registry_sha256):
    guard_root(); trusted_dir()
    if not HEX.fullmatch(expected_registry_sha256 or ''):
        raise ValueError('Exact verified original TEST registry hash required')
    uid=pwd.getpwnam('assertion-test-deploy').pw_uid
    if uid<=0:raise ValueError('Wrong canonical TEST account')
    with activation_lock(uid):
        prefix=ensure_current(uid)
        raw,st,doc,registry=existing()
        if registry or not absent(KEY) or sha256(raw).hexdigest()!=expected_registry_sha256:
            raise ValueError('Existing or ambiguous enrollment; verify without generating a second key')
        secret=secrets.token_hex(32).encode('ascii')
        digest=sha256(secret).hexdigest()
        after=enroll_payload(doc,registry,digest)
        assign_root_file(KEY,secret,0o600,0,absent_only=True)
        assign_root_file(REGISTRY,after,stat.S_IMODE(st.st_mode),st.st_gid,prior=(raw,st))
        if (sha256(credential(KEY).encode('ascii')).hexdigest()!=digest
                or regular_bytes(REGISTRY,maximum=12000,uid=0)!=after):
            raise ValueError('TEST enrollment write/readback mismatch; no repeat')
        child=subprocess.run([*prefix,'restart',UNIT],capture_output=True,timeout=120,
              env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
        if child.returncode:
            raise ValueError('TEST config staged but Base restart not confirmed; do not repeat')
    return dict(stage='test',fingerprint_registered=True,existing_runner_only=True,
                credential_created=True,base_restart=True,catalog_preflight_pending=True)

def probe():
    guard_root();trusted_dir()
    existing()
    key=installed_client()
    proof=discover(loopback(key),release=TAG,generation=GENERATION)
    return dict(stage='test',tested_existing_machine_catalog=True,writes=False,**proof)

def main():
    a=argparse.ArgumentParser(description=__doc__)
    mode=a.add_mutually_exclusive_group()
    mode.add_argument('--enroll',action='store_true')
    mode.add_argument('--probe',action='store_true')
    a.add_argument('--expected-registry-sha256')
    x=a.parse_args()
    try:
        answer=(enroll(x.expected_registry_sha256) if x.enroll else probe() if x.probe else inspect())
        print(json.dumps(answer,sort_keys=True))
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        raise SystemExit('TEST operation unconfirmed; verify existing key/profile/runtime before retry') from None

if __name__=='__main__':main()
