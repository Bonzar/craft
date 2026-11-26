"""Source-only witness runner for one approved fictional TEST batch.

Uses an already provisioned TEST credential. Never enrolls a key, touches a
database directly, follows redirects, reads PROD, or prints source bodies.
Defaults strictly to read-only catalog discovery.
"""
import argparse
from contextlib import contextmanager
import fcntl
import pwd
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import json, os, re, sqlite3, stat
from pathlib import Path
import uuid

from install_profile import TEST_DB, parse, regular_bytes, scope
from profile_plan import release_generation

DIR=Path('/etc/assertion-release/test')
REGISTRY=DIR/'private-operations.json'
KEY=DIR/'qa-runner-machine.key'
CLIENT='qa-runner'
REV='bc687ccf977b15d8ccac1fb986b0fe17000d5efd'
ARCHIVE='479a1d0950ec9d40b1292e99e25192e5052952c0f769575a04a5aecb2effb755'
TAG='assertion-memory-v0.1.36'
GENERATION=release_generation(REV,ARCHIVE,TAG,'test')
PATH='/memory-api-test'
PORT=8893
MAX_PAYLOAD=512*1024
HEX=re.compile(r'[0-9a-f]{64}\Z')
TAG_PATTERN=re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+\Z')
UUID=re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
TOOLS={'memory_ingest_register','memory_ingest_report','memory_capture_batch','memory_status'}
CLEAR_LOCK=Path(TEST_DB+'.clear.lock')

@contextmanager
def existing_clear_lease():
    uid=pwd.getpwnam('assertion-test-deploy').pw_uid
    fd=os.open(CLEAR_LOCK,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if (uid<=0 or not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=uid
                or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size!=0
                or CLEAR_LOCK.resolve(strict=True)!=CLEAR_LOCK):
            raise ValueError('Invalid existing TEST epoch lock')
        fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        if not os.path.samestat(st,CLEAR_LOCK.lstat()):
            raise ValueError('Existing TEST epoch lock changed')
        yield
    finally:
        os.close(fd)

def credential(path):
    if not path.is_absolute() or path.resolve(strict=True)!=path:
        raise ValueError('Use the previously approved TEST client key file')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1
                or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size not in (64,65)):
            raise ValueError('Untrusted existing TEST client key file')
        key=f.read(66).removesuffix(b'\n').decode('ascii')
        if not HEX.fullmatch(key) or not os.path.samestat(st,path.lstat()):
            raise ValueError('Wrong TEST client key file')
        return key

def installed_client():
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('Use the existing protected TEST runner')
    registry=parse(regular_bytes(REGISTRY,maximum=12000,uid=0)).get('machine_clients')
    key=credential(KEY)
    if (type(registry) is not dict or set(registry)!={CLIENT}
            or sha256(key.encode('ascii')).hexdigest()!=registry[CLIENT]):
        raise ValueError('Existing TEST-only runner fingerprint does not match')
    return key

def loopback(key):
    def post(body):
        c=HTTPConnection('127.0.0.1',PORT,timeout=20)
        try:
            c.request('POST',PATH,body=json.dumps(body,separators=(',',':')).encode(),headers={
                'Host':'localhost:'+str(PORT),'Content-Type':'application/json','Accept':'application/json','Authorization':'Bearer '+key})
            r=c.getresponse()
            raw=r.read(MAX_PAYLOAD+1)
            if r.status!=200 or len(raw)>MAX_PAYLOAD:
                raise ValueError('TEST operation not confirmed')
            return json.loads(raw)
        finally:
            c.close()
    return post

def discover(post, *, release, generation):
    if not TAG_PATTERN.fullmatch(release) or not HEX.fullmatch(generation):
        raise ValueError('Independently verified TEST release/generation required')
    rid=str(uuid.uuid4())
    response=post({'request_id':rid,'operation':'catalog','arguments':{}})
    if (response.get('request_id')!=rid or response.get('environment')!='test'
            or response.get('capability')!='machine' or response.get('release')!=release
            or response.get('generation')!=generation
            or not HEX.fullmatch(response.get('catalog_sha256',''))):
        raise ValueError('Wrong release, lane or authority; no writes')
    catalogue=response['tools']
    for name in TOOLS:
        if sum(i.get('name')==name for i in catalogue)!=1:
            raise ValueError('Approved TEST batch route is not available')
    return {k:response[k] for k in ('release','generation','catalog_sha256')}

def witness(post,*, proof, previous_scope, reset_scope, witness_id, read_scope=scope):
    if (not HEX.fullmatch(previous_scope) or not HEX.fullmatch(reset_scope)
            or previous_scope==reset_scope or not UUID.fullmatch(witness_id)
            or read_scope()!=reset_scope):
        raise ValueError('Current TEST epoch is not the exact independently witnessed reset')
    def call(operation, arguments):
        rid=str(uuid.uuid4())
        request=dict(request_id=rid,operation=operation,arguments=arguments,expected=proof)
        r=post(request)
        if (r.get('request_id')!=rid or r.get('environment')!='test' or r.get('status')!='ok'
                or any(r.get(k)!=v for k,v in proof.items()) or type(r.get('result')) is not dict):
            raise ValueError('Wrong TEST operation receipt')
        return r['result']

    baseline=call('memory_status',{'section':'overview','limit':1})
    if baseline.get('event_count')!=0 or baseline.get('thought_count')!=0:
        raise ValueError('Shared TEST is no longer empty; do not upload or retry clear')
    origin_id='qa-clear-witness-'+witness_id
    stream=call('memory_ingest_register',dict(producer_id='qa-clear-witness',session_id=origin_id))
    writer={k:stream[k] for k in ('stream_id','epoch')}
    if (not all(type(v) is str and UUID.fullmatch(v) for v in writer.values())
            or stream.get('acked_through')!=0 or stream.get('observed_seq')!=0 or stream.get('report_seq')!=0):
        raise ValueError('Expected a fresh post-clear fixture stream')
    report=dict(**writer,observed_seq=2,scan_state='current',gap_count=0)
    call('memory_ingest_report',dict(**report,report_seq=1))
    messages=[dict(session_id=origin_id,message_id=origin_id+':'+role,actor_id='qa-clear-'+role,
        role=role,text=text,source='qa-clear-visual-witness',scope='qa-clear-witness',
        delivery=dict(**writer,seq=i))
      for i,(role,text) in enumerate((('user','Фиктивный проект: бумажная птица отмечена синим цветом.'),
              ('assistant','Записал описание вымышленной бумажной птицы.')),1)]
    batch_id=sha256((reset_scope+'\x00'+origin_id).encode()).hexdigest()
    captured=call('memory_capture_batch',dict(batch_id=batch_id,messages=messages))
    events=[r['event']['id'] for r in captured.get('receipts',[])]
    if captured.get('batch_id')!=batch_id or len(events)!=2 or len(set(events))!=2:
        raise ValueError('No exact one-batch/two-member receipt; do not repeat an ambiguous effect')
    closed=call('memory_ingest_report',dict(**report,report_seq=2,lifecycle='closed'))
    if closed.get('lifecycle')!='closed' or closed.get('acked_through')!=2:
        raise ValueError('TEST fixture stream closure not confirmed; do not repeat clear')
    after=call('memory_status',{'section':'overview','limit':1})
    if after.get('event_count')!=2 or read_scope()!=reset_scope:
        raise ValueError('TEST contains different activity; investigate without replay')
    return dict(stage='test',release=proof['release'],generation=proof['generation'],batch_id=batch_id,
        member_count=2,before_event_count=0,after_event_count=2,stream_closed=True,
        old_replay_observed=False,scope_confirmed=True)

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--previous-scope');a.add_argument('--reset-scope');a.add_argument('--witness-id')
    a.add_argument('--write-one-after-visual-clear',action='store_true')
    args=a.parse_args()
    try:
        post=loopback(installed_client())
        pin=discover(post,release=TAG,generation=GENERATION)
        if args.write_one_after_visual_clear:
            with existing_clear_lease():
                state=witness(post,proof=pin,previous_scope=args.previous_scope,reset_scope=args.reset_scope,
                              witness_id=args.witness_id)
        else:
            state=dict(stage='test',approved_existing_batch_catalog=True,writes=False,**pin)
        print(json.dumps(state,sort_keys=True))
    except (OSError,ValueError,HTTPException,KeyError,TypeError,sqlite3.Error):
        # No exceptions/credentials/request or response bodies in stdout.
        raise SystemExit('TEST witness not confirmed; inspect exact receipts without retrying effects') from None

if __name__=='__main__':main()
