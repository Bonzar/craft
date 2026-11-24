"""Approved workspace composition; adding a project never adds a workflow job."""
import importlib.util
import json
import sys


def registry(root, ops):
    qa=root/'assertion-memory-qa'
    vis=root/'assertion-memory-visual'
    sys.path[:0]=[str(vis),str(qa)]
    from configured_base import BASE
    import configured_visual_profile as visual

    def base_unpack(source,output,revision):
        spec=importlib.util.spec_from_file_location('configured_base_qa_lifecycle',qa/'lifecycle.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ci.unpack_zip(source,output,revision)

    def base_env(env):
        return {**env,
            'CONFIGURED_BASE_HOST':env['RELEASE_SSH_HOST'],
            'CONFIGURED_BASE_PORT':env['RELEASE_SSH_PORT'],
            'CONFIGURED_BASE_SSH_HOST_PIN':env['RELEASE_SSH_HOST_PIN'],
            'CONFIGURED_BASE_SSH_KEY':env['RELEASE_SSH_KEY'],
            'CONFIGURED_BASE_VM_ID':env['RELEASE_VM_ID'],
            'CONFIGURED_BASE_ENV_NODE_ID':env['RELEASE_ENV_NODE_ID'],
            'CONFIGURED_BASE_REUSABLE_SHA':env['GITHUB_SHA'],
            'CONFIGURED_BASE_TEST_READY':env['RELEASE_ENABLE'],
        }

    def base_activate(ctrl,release,content,payload,promotion,env):
        if ctrl.stage=='test':
            import configured_base_test_provider as base
            return base.activate(ctrl,release,content,payload,promotion,base_env(env))
        import configured_base_prod_provider as base
        source={**env,'RELEASE_ENVIRONMENT_NODE_ID':env['RELEASE_ENV_NODE_ID']}
        receipt=base.activate(ctrl,release,content,payload,promotion,source)
        with (payload/'configured-base-prod-receiver.json').open('x',encoding='ascii') as out:
            json.dump(receipt,out,sort_keys=True,separators=(',',':'))
        return receipt

    def base_smoke(ctrl,release,content,payload,env):
        if ctrl.stage=='test':
            import configured_base_test_provider as base
            return base.smoke(ctrl,release,content,payload,base_env(env))
        # The existing canonical PROD writer already checks process/graph/MCP
        # before acknowledging; do not invoke the TEST command on PROD.
        got=json.loads((payload/'configured-base-prod-receiver.json').read_text(encoding='ascii'))
        expected={'status':'ok','environment':'production','sha256':content.sha256,'revision':release.revision}
        if got!=expected:
            raise ValueError('Authenticated PROD smoke receipt not verified')

    def visual_unpack(source,output,revision):
        return visual.content(visual.unpack_verified(source,output,revision))

    def visual_activate(ctrl,release,content,payload,promotion,env):
        transport={
            'host':env['RELEASE_SSH_HOST'],'port':env['RELEASE_SSH_PORT'],
            'host_pin':env['RELEASE_SSH_HOST_PIN'],'ssh_key':env['RELEASE_SSH_KEY'],
            'signing_key':env['RELEASE_SIGNING_KEY'],'runner_temp':env['RUNNER_TEMP'],
            'account':ctrl.target.receiver_account,
        }
        return visual.activate(ctrl.stage,visual.Payload(payload,release.revision,content.sha256,content.manifest_sha256),transport)

    def visual_smoke(ctrl,release,content,payload,env):
        gateway={k:env['RELEASE_'+k.upper()] for k in ('gateway_host','gateway_token')}
        visual.verify_smoke(ctrl.stage,visual.Payload(payload,release.revision,content.sha256,content.manifest_sha256),gateway)

    profiles={'base':BASE,'visual':visual.PROFILE}
    operations={
        'base':ops('release.tar.gz',base_unpack,base_activate,base_smoke,
                   ('assertion-memory-deploy-test','assertion-memory-deploy-production')),
        'visual':ops('visual.tar',visual_unpack,visual_activate,visual_smoke,
                     ('assertion-memory-visual-test','assertion-memory-visual-prod')),
    }
    if profiles.keys()!=operations.keys():
        raise ValueError('Incomplete registered project map')
    return profiles,operations
