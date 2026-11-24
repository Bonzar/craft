"""Configured Base PROD runner; receives an already checked Common Promotion."""
import hashlib
from pathlib import Path
from contextlib import contextmanager
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'prod-bridge'))
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'assertion-memory-release'))
from prod_runner import exchange_factory, mint
import ssh_transport
from reusable_proof import require
from configured_base_prod_gate import BASE, Policy, audience, intent_document, permit

def operate(exchange, policy, promotion, path, *, get_token=mint):
    doc=intent_document(promotion)
    with path.open('rb') as archive:
        require(hashlib.file_digest(archive,'sha256').hexdigest()==promotion.content.sha256,'Wrong archive')
        archive.seek(0)
        cmd='configured-prod-receive'
        challenge=exchange(cmd,{'op':'challenge','intent':doc},None)
        require(set(challenge)=={'status','nonce'} and challenge['status']=='ok','Invalid challenge')
        nonce=challenge['nonce']
        token=get_token(audience(policy,promotion,nonce))
        got=exchange(cmd,{'op':'confirm','intent':doc,'nonce':nonce,'token':token},archive)
        require(got==permit(promotion),'Wrong PROD receipt')
        return got

def activate(ctrl,release,content,payload,promotion,env):
    require(ctrl.profile is BASE and ctrl.stage=='prod' and promotion is not None
            and (ctrl.run,ctrl.control_sha,ctrl.trigger)==(promotion.run_id,promotion.control_sha,promotion.trigger)
            and (release,content)==(promotion.release,promotion.content),'Common PROD proof required')
    policy=Policy(env['GITHUB_REPOSITORY_ID'],env['RELEASE_APPROVED_CONTROL_SHA'],env['RELEASE_VM_ID'],
                  env['RELEASE_OIDC_SUBJECT'],env['RELEASE_ENVIRONMENT_NODE_ID'],
                  frozenset({env['GITHUB_ACTOR_ID']}))
    require(policy.control_sha==ctrl.control_sha,'Unaudited reusable workflow commit')
    with route(env) as argv:
        return operate(exchange_factory(argv),policy,promotion,payload/'release.tar.gz',
                       get_token=lambda aud:mint(aud,env=env))

@contextmanager
def route(env):
    shape='assertion-production-deploy'
    account='assertion-cfg-base-prod-deploy'
    if BASE.prod.receiver_account!=account:
        raise ValueError('Configured PROD account contract changed')
    host=env['RELEASE_SSH_HOST']
    with ssh_transport.route(host=host,port=env['RELEASE_SSH_PORT'],account=shape,
            host_pin=env['RELEASE_SSH_HOST_PIN'],key=env['RELEASE_SSH_KEY'],
            runtime_dir=env['RUNNER_TEMP']) as argv:
        if argv[0]!='/usr/bin/ssh' or argv[-2:]!=['--',shape+'@'+host]:
            raise ValueError('Invalid local SSH arguments')
        yield [*argv[:-1],account+'@'+host]
