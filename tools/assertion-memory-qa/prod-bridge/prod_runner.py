"""Runner protocol. Called only by an exact-SHA protected reusable PROD job."""
from dataclasses import asdict
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener, ProxyHandler

from authorizer import NoRedirect
from reusable_proof import CALLER, DIGEST, ENVIRONMENT, Intent, Policy, audience, obj, plain, require

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'assertion-memory-release'))
import ssh_transport

# As documented runner endpoint; do not send the bearer to caller-supplied hosts.
REQUEST_HOST='pipelines.actions.githubusercontent.com'


def mint(aud, *, env=os.environ):
    before=urlsplit(env['ACTIONS_ID_TOKEN_REQUEST_URL'])
    require(before.scheme=='https' and before.netloc==REQUEST_HOST and before.path.startswith('/')
            and not before.fragment,'Invalid GitHub OIDC issuance origin')
    query=parse_qsl(before.query,keep_blank_values=True)
    require(not any(k=='audience' for k,_ in query),'Unexpected existing audience')
    url=urlunsplit(before._replace(query=urlencode(query+[('audience',aud)])))
    request=Request(url,headers={'Authorization':'Bearer '+env['ACTIONS_ID_TOKEN_REQUEST_TOKEN'],
                                 'Accept':'application/json'})
    with build_opener(ProxyHandler({}),NoRedirect).open(request,timeout=5) as reply:
        require(reply.status==200 and reply.geturl()==url,'Unexpected token issuance response')
        raw=reply.read(20_001)
    require(len(raw)<=20_000,'Token issuance response too large')
    token=obj(raw)['value']
    require(plain(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',token)
            and len(token)<=16_384,'Malformed issued token')
    return token


def operate(ssh, policy, intent, archive_path, *, get_token=mint):
    with archive_path.open('rb') as archive:
        require(hashlib.file_digest(archive,'sha256').hexdigest()==intent.sha256,'Archive damaged or switched')
        archive.seek(0)
        challenge=ssh('authorize-release production',{'intent':asdict(intent)},None)
        require(challenge.keys()=={'status','nonce'} and challenge['status']=='ok'
                and plain(DIGEST,challenge['nonce']),'Invalid production challenge')
        nonce=challenge['nonce']
        token=get_token(audience(policy,intent,nonce))
        receipt=ssh(f'deploy-release production {intent.revision} {intent.sha256}',
                    {'intent':asdict(intent),'nonce':nonce,'token':token},archive)
    require(receipt=={'status':'ok','environment':'production','revision':intent.revision,'sha256':intent.sha256},
            'Host did not activate and smoke-check exact PROD bytes')
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
        ssh_transport.preflight(route)
        receipt=operate(exchange_factory(route),policy,intent,path/'release.tar.gz')
    print('Activated and verified production:',receipt['revision'],receipt['sha256'])


if __name__=='__main__':
    try:
        main()
    except Exception:
        # In particular, never print HTTP exception bodies, SSH errors, or a JWT.
        raise SystemExit('Protected production release failed closed') from None
