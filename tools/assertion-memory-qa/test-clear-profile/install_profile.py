"""Reviewed TEST operator primitive. Never writes to a database or calls a web API.

Run only from the pre-existing authorized TEST host ops entry, after protected
source publication. It is deliberately not an approval or authentication grant.
"""
from contextlib import closing, contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import sqlite3
import stat
import subprocess

from profile_plan import TEST_BASE, TEST_DB, TEST_MARKER, TEST_ORIGIN, TEST_UNIT, plan

RELEASE=Path(TEST_BASE).parent
SYSCTL='/usr/bin/systemctl'
RUNUSER='/usr/sbin/runuser'
SOURCES=('apps/graph_viewer/server/__main__.py','apps/graph_viewer/server/browser_owner_api.py')
JOURNAL=RELEASE/'.activation-pending-v1'

def parse(data):
    def unique(pairs):
        d=dict(pairs)
        if len(d)!=len(pairs):
            raise ValueError('Duplicate operator evidence field')
        return d
    return json.loads(data,object_pairs_hook=unique)

def regular_bytes(path, *, maximum, uid=None, mode=None):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>maximum
                or st.st_mode&0o022 or uid is not None and st.st_uid!=uid
                or mode is not None and stat.S_IMODE(st.st_mode)!=mode
                or path.resolve(strict=True)!=path):
            raise ValueError('Untrusted fixed TEST file')
        data=stream.read(maximum+1)
        if len(data)!=st.st_size or not os.path.samestat(st,path.stat()):
            raise ValueError('TEST file changed')
    return data

@contextmanager
def activation_lock(uid):
    path=RELEASE/'deploy.lock'
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=uid
                or st.st_nlink!=1 or st.st_mode&0o077):
            raise ValueError('Untrusted TEST activation lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not os.path.samestat(st,path.lstat()):
            raise ValueError('TEST activation lock changed')
        yield
    finally:
        os.close(fd)

def scope():
    db=Path(TEST_DB)
    st=db.lstat()
    if db.resolve(strict=True)!=db or not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:
        raise ValueError('Wrong canonical TEST database')
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.25)) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        has=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_database_clear_state'").fetchone()
        epoch='0'*32
        if has:
            rows=c.execute("SELECT revision FROM memory_database_clear_state WHERE singleton=1").fetchall()
            if len(rows)!=1 or type(rows[0][0]) is not str or not re.fullmatch('[a-f0-9]{32}',rows[0][0]):
                raise ValueError('Unknown TEST database epoch')
            epoch=rows[0][0]
    if not os.path.samestat(st,db.lstat()):
        raise ValueError('TEST database changed')
    return sha256(json.dumps(['database-clear-scope-v1','test',st.st_dev,st.st_ino,epoch],
        separators=(',',':')).encode()).hexdigest()

def verify_installed_sources(current, expected_manifest_sha256):
    code={}
    raw=regular_bytes(current/'SHA256SUMS',maximum=5*1024*1024)
    if sha256(raw).hexdigest()!=expected_manifest_sha256:
        raise ValueError('Unverified independent TEST manifest pin')
    manifest=raw.decode('ascii')
    lines=[line.partition('  ') for line in manifest.splitlines()]
    required=(*SOURCES,'REVISION','BASE_RELEASE_TAG')
    for source in required:
        found=[entry[0] for entry in lines if entry[1] and entry[2] in (source,'./'+source)]
        if len(found)!=1 or not re.fullmatch('[a-f0-9]{64}',found[0]):
            raise ValueError('Missing exact TEST package source')
        data=regular_bytes(current/source,maximum=96*1024)
        if sha256(data).hexdigest()!=found[0]:
            raise ValueError('TEST source seal mismatch')
        code[source]=data
    if b'installed_test_clear_profile' not in code[SOURCES[0]] or b'browser-owner-clear-profile.json' not in code[SOURCES[1]]:
        raise ValueError('Active TEST package lacks the reviewed profile source')
    return code

def verify_graph(uid,current):
    prefix=[RUNUSER,'-u','assertion-test-deploy','--','/usr/bin/env','XDG_RUNTIME_DIR=/run/user/'+str(uid),
            SYSCTL,'--user']
    out=subprocess.run([*prefix,'show',TEST_UNIT,'-p','ActiveState','-p','MainPID'],capture_output=True,check=True,
                      timeout=8,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'}).stdout.decode('ascii')
    values=dict(row.split('=',1) for row in out.splitlines() if '=' in row)
    if values.get('ActiveState')!='active' or not re.fullmatch('[1-9][0-9]{0,9}',values.get('MainPID','')):
        raise ValueError('Canonical TEST Graph is not active')
    pid=int(values['MainPID'])
    proc=Path('/proc')/str(pid)
    if proc.joinpath('cwd').resolve(strict=True)!=current or proc.stat().st_uid!=uid:
        raise ValueError('TEST Graph release or process identity changed')
    rows=proc.joinpath('status').read_text().splitlines()
    uids=next(line.partition(':')[2].split() for line in rows if line.startswith('Uid:'))
    if uids[:2]!=[str(uid)]*2:
        raise ValueError('Wrong TEST Graph process UID')
    args=proc.joinpath('cmdline').read_bytes().split(b'\x00')[:-1]
    expected={b'--source':TEST_DB.encode(),b'--public-origin':TEST_ORIGIN.encode(),
              b'--queue-status-stage':b'test',
              b'--queue-status-config':b'/run/assertion-memory-dashboard/test/scheduler.json'}
    if (args.count(b'--browser-owner-api')!=1 or b'--browser-owner-clear' in args
            or any(args.count(key)!=1 or args.index(key)+1>=len(args) or
                   args[args.index(key)+1]!=value for key,value in expected.items())):
        raise ValueError('Unexpected TEST Graph authority arguments')
    return prefix

def atomic_absent(path,body):
    parent=path.parent
    for p in (parent,parent.parent):
        st=p.lstat()
        if p.resolve(strict=True)!=p or not stat.S_ISDIR(st.st_mode) or st.st_uid or st.st_mode&0o022:
            raise ValueError('Unsafe fixed TEST marker parent')
    fd=os.open(parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    name='.test-clear-'+secrets.token_hex(16)
    created=False
    try:
        tmpfd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
                      0o600,dir_fd=fd)
        created=True
        with os.fdopen(tmpfd,'wb') as staged:
            staged.write(body)
            staged.flush()
            os.fchmod(staged.fileno(),0o644)
            os.fsync(staged.fileno())
        os.link(name,path.name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
    finally:
        try:
            if created:
                os.unlink(name,dir_fd=fd)
                os.fsync(fd)
        finally:
            os.close(fd)
    if regular_bytes(path,maximum=512,uid=0,mode=0o644)!=body:
        raise ValueError('Installed TEST marker failed readback')

def install(evidence, *, revision, archive, tag, expected_evidence_sha256, expected_manifest_sha256, expected_marker_sha256, database_scope=None):
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('Existing protected TEST host authority is required')
    output=plan(evidence,revision=revision,archive=archive,tag=tag,
                expected_evidence_sha256=expected_evidence_sha256,expected_manifest_sha256=expected_manifest_sha256,
                database_scope=database_scope)
    if output['marker_sha256']!=expected_marker_sha256:
        raise ValueError('Candidate bytes differ from independently reviewed digest')
    uid=pwd.getpwnam('assertion-test-deploy').pw_uid
    if uid<=0 or uid!=evidence['configured_uid']:
        raise ValueError('TEST account changed since review')
    with activation_lock(uid):
        try:
            JOURNAL.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError('TEST activation journal present')
        current=(RELEASE/'current').resolve(strict=True)
        if current!=Path(evidence['current']) or scope()!=evidence['database_scope']:
            raise ValueError('TEST release or database changed since review')
        code=verify_installed_sources(current,expected_manifest_sha256)
        if code['REVISION'].strip().decode()!=revision or code['BASE_RELEASE_TAG'].strip().decode()!=tag:
            raise ValueError('TEST release source and tags differ')
        verify_graph(uid,current)
        atomic_absent(Path(TEST_MARKER),output['profile'])
    # Existing protected Graph restart/health is a distinct serialized stage.
    return {k:v for k,v in output.items() if k!='profile'}|{'writes_performed':True,'runtime_activation_pending':True}

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',required=True,type=Path)
    parser.add_argument('--evidence-sha256',required=True)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--archive',required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--database-scope')
    parser.add_argument('--marker-sha256')
    parser.add_argument('--apply',action='store_true')
    opts=parser.parse_args()
    try:
        data=regular_bytes(opts.evidence,maximum=8192,uid=0,mode=0o600)
        evidence=parse(data)
        args=dict(revision=opts.revision,archive=opts.archive,tag=opts.tag,
                  expected_evidence_sha256=opts.evidence_sha256,expected_manifest_sha256=opts.manifest_sha256,
                  database_scope=opts.database_scope)
        if opts.apply:
            report=install(evidence,expected_marker_sha256=opts.marker_sha256,**args)
        else:
            report={k:v for k,v in plan(evidence,**args).items() if k!='profile'}
        print(json.dumps(report,sort_keys=True))
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,subprocess.SubprocessError):
        raise SystemExit('TEST clear profile preflight/apply refused; read back exact state before retry') from None

if __name__=='__main__':
    main()
