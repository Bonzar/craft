"""Pure renderer for the existing TEST Graph unit; does not access or mutate a host."""
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

ROOT='/srv/assertion-release/test'
UNIT='assertion-test-deploy-graph.service'
DROP_IN='zzzzzz-base-browser-owner-api-v1.conf'
ACCOUNT='assertion-test-deploy'
REVISION='5368f287176d266c8c8425877df6f6f42bb33f10'
ARCHIVE='8ce1c104d1df3bacdcc51457e02458560f6d389b469260d061732b969fb5e19a'
SMOKE_V2='31583c3ef2dd37aa859f34ca3bb139f836f5848c6325ada624857ce6d40064b9'
SSE_DROPIN={'98-test-direct-event-rehearsal.conf':
    'c3f027f44160148514802b3e23cd398c76a437019e6da6d4c63f0495cd5ff643'}
ORIGIN='https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'
PROJECTION='/run/assertion-memory-dashboard/test/scheduler.json'
SOURCE=ROOT+'/state/fictional.sqlite3'
VIEWER_STATE=ROOT+'/state/graph-state'
PASSWORD=ROOT+'/state/graph-password'
STATIC=ROOT+'/current/apps/graph_viewer/web/dist'
INTERPRETER=ROOT+'/current/venv/bin/python'
HEX=re.compile(r'[0-9a-f]{64}\Z')
ATOM=re.compile(r'[A-Za-z0-9._:/-]+\Z')
ALLOWED_VALUES={
    '--source','--state-dir','--password-file','--static-dir','--host','--port',
    '--public-origin','--test-direct-events-origin','--queue-status-config','--queue-status-stage',
    '--provider-id','--embedding-model','--embedding-cache',
}
ALLOWED_FLAGS={'--synthetic-fixture'}
EXPECTED={
    '--source':SOURCE,'--state-dir':VIEWER_STATE,'--password-file':PASSWORD,
    '--static-dir':STATIC,'--port':'8891','--public-origin':ORIGIN,
}
EVIDENCE={
 'unit','account','uid','effective_uid','active_pid','listener','cwd','current','effective_argv',
 'drop_in_absent','installed_dropins','smoke_helper_sha256','dashboard_pair_current','baseline_graph_smoke',
 'browser_mode_dark','clear_dark','legacy_graph_authority_absent'
}

def digest(value):
    return hashlib.sha256(value).hexdigest()

def spec_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def valid_pin(value):
    return type(value) is str and bool(HEX.fullmatch(value))

def derive(before, *, expected_archive, expected_uid, expected_before_sha256,
           expected_smoke_helper_sha256):
    if (not valid_pin(expected_before_sha256) or expected_archive!=ARCHIVE
            or expected_smoke_helper_sha256!=SMOKE_V2):
        raise ValueError('independent archive, smoke helper and baseline pins required')
    if type(before) is not dict or set(before)!=EVIDENCE or digest(spec_bytes(before))!=expected_before_sha256:
        raise ValueError('effective TEST baseline drift')
    canonical=ROOT+'/releases/'+REVISION+'-'+expected_archive
    if (type(expected_uid) is not int or expected_uid<=0 or type(before['uid']) is not int
            or type(before['effective_uid']) is not int or before['uid']!=expected_uid
            or before['effective_uid']!=expected_uid or type(before['active_pid']) is not int
            or before['active_pid']<=1 or (before['unit'],before['account'])!=(UNIT,ACCOUNT)
            or before['listener']!='127.0.0.1:8891' or before['cwd']!=canonical
            or before['current']!=canonical or before['smoke_helper_sha256']!=expected_smoke_helper_sha256
            or before['installed_dropins']!=SSE_DROPIN):
        raise ValueError('wrong live TEST unit, identity, listener or archive')
    for gate in ('drop_in_absent','dashboard_pair_current','baseline_graph_smoke',
                 'browser_mode_dark','clear_dark','legacy_graph_authority_absent'):
        if before[gate] is not True:
            raise ValueError('TEST activation precondition not established')
    args=before['effective_argv']
    if (type(args) is not list or not 4<len(args)<=34 or args[:4]!=
            [INTERPRETER,'-B','-m','apps.graph_viewer.server']
            or any(type(x) is not str or len(x)>330 or not ATOM.fullmatch(x)
                   or ('/' in x and any(part in {'.','..'} for part in x.split('/'))) for x in args)):
        raise ValueError('unrecognized TEST Graph ExecStart syntax')
    flags={};i=4
    while i<len(args):
        key=args[i];i+=1
        if key in flags or key not in ALLOWED_VALUES|ALLOWED_FLAGS:
            raise ValueError('unexpected or repeated TEST Graph flag')
        if key in ALLOWED_FLAGS:
            flags[key]=True
            continue
        if i==len(args) or args[i].startswith('--'):
            raise ValueError('missing TEST Graph flag value')
        flags[key]=args[i];i+=1
    if (any(flags.get(k)!=v for k,v in EXPECTED.items())
            or flags.get('--host','127.0.0.1')!='127.0.0.1'
            or flags.get('--synthetic-fixture') is not True):
        raise ValueError('wrong TEST Graph state, trusted origin or listener')
    if ('--embedding-model' in flags)!=('--embedding-cache' in flags):
        raise ValueError('embedding config pair must stay together')
    if '--embedding-cache' in flags and not flags['--embedding-cache'].startswith(ROOT+'/state/'):
        raise ValueError('embedding cache must belong to TEST')
    if '--provider-id' in flags and flags['--provider-id']!='assertion-memory':
        raise ValueError('unexpected Graph provider')
    direct=flags.get('--test-direct-events-origin')
    try:
        parsed=urlsplit(direct or '')
        address=ipaddress.IPv4Address(parsed.hostname)
        if not address.is_global or direct!='https://'+str(address):
            raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('preserve an existing exact TEST direct SSE origin') from None
    pair=(flags.get('--queue-status-config'),flags.get('--queue-status-stage'))
    if pair==(None,None):
        delta=['--queue-status-config',PROJECTION,'--queue-status-stage','test','--browser-owner-api']
    elif pair==(PROJECTION,'test'):
        delta=['--browser-owner-api']
    else:
        raise ValueError('inconsistent or cross-lane queue projection pair')
    candidate=[*args,*delta]
    dropin=('# Base same-origin browser API on the existing TEST Graph listener only.\n'
            '[Service]\nExecStart=\nExecStart='+' '.join(candidate)+'\n').encode('ascii')
    return {'unit':UNIT,'basename':DROP_IN,'dropin':dropin,'sha256':digest(dropin),
            'expected_argv':candidate,'rollback_argv':args,'rollback':'remove_exact_dropin_and_restart_same_graph_only'}

def main():
    import argparse
    import os
    from pathlib import Path
    import stat
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('evidence',type=Path)
    p.add_argument('--archive',required=True)
    p.add_argument('--uid',required=True,type=int)
    p.add_argument('--baseline-sha256',required=True)
    p.add_argument('--smoke-sha256',required=True)
    args=p.parse_args()
    try:
        if not args.evidence.is_absolute() or args.evidence.resolve(strict=True)!=args.evidence:
            raise ValueError()
        fd=os.open(args.evidence,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as inp:
            st=os.fstat(inp.fileno())
            if (not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_nlink!=1
                    or stat.S_IMODE(st.st_mode)!=0o600 or not 0<st.st_size<=16*1024):
                raise ValueError()
            raw=inp.read(16*1024+1)
            if len(raw)!=st.st_size or not os.path.samestat(st,args.evidence.lstat()):
                raise ValueError()
        def unique(pairs):
            out={}
            for name,value in pairs:
                if name in out:
                    raise ValueError()
                out[name]=value
            return out
        plan=derive(json.loads(raw,object_pairs_hook=unique),expected_archive=args.archive,
                   expected_uid=args.uid,expected_before_sha256=args.baseline_sha256,
                   expected_smoke_helper_sha256=args.smoke_sha256)
        print(json.dumps({'unit':plan['unit'],'dropin_basename':plan['basename'],
                          'baseline_sha256':args.baseline_sha256,'planned_sha256':plan['sha256'],
                          'writes':False}))
    except (OSError,ValueError,TypeError):
        raise SystemExit('TEST Graph activation review refused') from None

if __name__=='__main__':
    main()
