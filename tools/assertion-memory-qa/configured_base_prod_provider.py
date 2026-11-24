"""Configured Base PROD runner; receives an already checked Common Promotion."""
import hashlib
from pathlib import Path
import tempfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'prod-bridge'))
from prod_runner import exchange_factory, guarded_route, mint
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
    aliases={'RELEASE_HOST':env['RELEASE_SSH_HOST'],'RELEASE_PORT':env['RELEASE_SSH_PORT'],
             'SSH_HOST_PIN':env['RELEASE_SSH_HOST_PIN'],'SSH_PRIVATE_KEY':env['RELEASE_SSH_KEY']}
    with tempfile.TemporaryDirectory(prefix='configured-base-prod-',dir=env['RUNNER_TEMP']) as work:
        route=guarded_route(aliases,Path(work))
        require(route[-1]=='assertion-production-deploy@'+aliases['RELEASE_HOST'],'Legacy host pinning contract drift')
        route[-1]=BASE.prod.receiver_account+'@'+aliases['RELEASE_HOST']
        return operate(exchange_factory(route),policy,promotion,payload/'release.tar.gz')
