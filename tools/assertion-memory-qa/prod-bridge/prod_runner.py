"""Runner protocol. Called only by an exact-SHA protected reusable PROD job."""
from dataclasses import asdict
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit
from urllib.request import Request, build_opener, ProxyHandler

from authorizer import NoRedirect
from reusable_proof import CALLER, DIGEST, ENVIRONMENT, Intent, Policy, audience, obj, plain, require

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'assertion-memory-release'))
import ssh_transport

# As documented runner endpoint; do not send the bearer to caller-supplied hosts.
REQUEST_HOST=re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.actions\.githubusercontent\.com')


class ProtectedStageError(Exception):
    """Only compile-time diagnostic names cross from runner to log."""


def stage(name, fn):
    try:
        return fn()
    except ProtectedStageError:
        raise
    except Exception:
        raise ProtectedStageError(name) from None


def issuance_url(raw_url, aud):
    require(isinstance(raw_url,str) and 0<len(raw_url)<=8192
            and all(33<=ord(c)<=126 for c in raw_url)
            and '#' not in raw_url and chr(92) not in raw_url,
            'Invalid GitHub OIDC issuance URL')
    before=urlsplit(raw_url)
    require(raw_url.startswith('https://') and before.scheme=='https'
            and REQUEST_HOST.fullmatch(before.netloc)
            and before.path.startswith('/') and bool(before.query)
            and not before.fragment,'Invalid GitHub OIDC issuance origin')
    query=parse_qsl(before.query,keep_blank_values=True,max_num_fields=64)
    require(not any(k=='audience' for k,_ in query),'Unexpected existing audience')
    # The runner-issued query is opaque; GitHub Toolkit appends without normalizing it.
    return raw_url+'&audience='+quote(aud,safe='')


def mint(aud, *, env=os.environ):
    url=stage('github-oidc-issuance-origin',lambda: issuance_url(env['ACTIONS_ID_TOKEN_REQUEST_URL'],aud))
    request=Request(url,headers={'Authorization':'Bearer '+env['ACTIONS_ID_TOKEN_REQUEST_TOKEN'],
                                 'Accept':'application/json'})
    def fetch():
        with build_opener(ProxyHandler({}),NoRedirect).open(request,timeout=5) as reply:
            require(reply.status==200 and reply.geturl()==url,'Unexpected token issuance response')
            return reply.read(20_001)
    raw=stage('github-oidc-issuance-http',fetch)
    def parse():
        require(len(raw)<=20_000,'Token issuance response too large')
        token=obj(raw)['value']
        require(plain(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',token)
                and len(token)<=16_384,'Malformed issued token')
        return token
    return stage('github-oidc-issuance-response',parse)


def operate(ssh, policy, intent, archive_path, *, get_token=mint):
    with archive_path.open('rb') as archive:
        require(hashlib.file_digest(archive,'sha256').hexdigest()==intent.sha256,'Archive damaged or switched')
        archive.seek(0)
        challenge=stage('host-challenge-transport',lambda: ssh(
            'authorize-release production',{'intent':asdict(intent)},None))
        stage('host-challenge-response',lambda: require(
            challenge.keys()=={'status','nonce'} and challenge['status']=='ok'
            and plain(DIGEST,challenge['nonce']),'Invalid production challenge'))
        nonce=challenge['nonce']
        token=stage('github-oidc-issuance',lambda: get_token(audience(policy,intent,nonce)))
        receipt=stage('host-activation-transport',lambda: ssh(
            f'deploy-release production {intent.revision} {intent.sha256}',
            {'intent':asdict(intent),'nonce':nonce,'token':token},archive))
    stage('host-activation-receipt',lambda: require(
        receipt=={'status':'ok','environment':'production','revision':intent.revision,'sha256':intent.sha256},
        'Host did not activate and smoke-check exact PROD bytes'))
    return receipt


def exchange_factory(route):
    def ssh(command,envelope,archive):
        frame=json.dumps(envelope,separators=(',',':')).encode('ascii')+b'\n'
        code,wire=ssh_transport.exchange(route,command,envelope=frame,payload=archive,limit=512)
        require(code==0 and wire.endswith(b'\n'),'Restricted PROD connection failed')
        return obj(wire)
    return ssh


def main(env=os.environ):
    require(env.get('RELEASE_ENABLED')=='true' and env.get('GITHUB_WORKFLOW_REF')==CALLER
            and env.get('GITHUB_REF')=='refs/heads/main' and env.get('GITHUB_JOB')=='deploy-production'
            and env.get('GITHUB_RUN_ATTEMPT')=='1' and env.get('GITHUB_EVENT_NAME')=='workflow_dispatch',
            'Protected production job required')
    path=Path(env['RELEASE_DOWNLOAD_DIR'])
    intent=Intent(**obj((path/'prod-intent.json').read_bytes()))
    require(intent.run_id==env.get('GITHUB_RUN_ID'),'Authorized input is not for the current dispatch')
    policy=Policy(env['GITHUB_REPOSITORY_ID'],env['AUDITED_REUSABLE_SHA'],env['PROD_VM_ID'],
                  env['PROD_OIDC_SUBJECT'],env['PROD_ENVIRONMENT_NODE_ID'],frozenset({env['GITHUB_ACTOR_ID']}))
    with ssh_transport.route(host=env['RELEASE_HOST'],port=env['RELEASE_PORT'],
             account='assertion-production-deploy',host_pin=env['SSH_HOST_PIN'],
             key=env['SSH_PRIVATE_KEY'],runtime_dir=env['RUNNER_TEMP']) as route:
        stage('ssh-restricted-probes',lambda: ssh_transport.preflight(route))
        receipt=operate(exchange_factory(route),policy,intent,path/'release.tar.gz')
    print('Activated and verified production:',receipt['revision'],receipt['sha256'])


if __name__=='__main__':
    try:
        main()
    except ProtectedStageError as exc:
        raise SystemExit('Protected production release failed closed: '+str(exc)) from None
    except Exception:
        # In particular, never print HTTP exception bodies, SSH errors, or a JWT.
        raise SystemExit('Protected production release failed closed') from None
