"""Approved workspace composition; adding a project never adds a workflow job."""
import importlib.util
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
        'base':ops('release.tar.gz',base_unpack,None,None,
                   ('assertion-memory-test','assertion-memory-production')),
        'visual':ops('visual.tar',visual_unpack,visual_activate,visual_smoke,
                     ('assertion-memory-visual-test','assertion-memory-visual-prod')),
    }
    if profiles.keys()!=operations.keys():
        raise ValueError('Incomplete registered project map')
    return profiles,operations
