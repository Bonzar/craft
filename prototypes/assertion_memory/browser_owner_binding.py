"""Source-only composition specimen; never loads settings or starts a server."""
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from apps.graph_viewer.server.graph_node_owner_api import require_verified_base_commit
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.queue_rest import snapshot

_ACTIONS = frozenset((
    'pause_prepare', 'pause_set', 'sessions_prepare', 'sessions_set',
    'policy_prepare', 'policy_set', 'item_preview', 'item_remove',
))

def base_owner_browser(*, stage, config, verified_identity, release_lease, interactive_enabled, machine_devices=None, node_writer_enabled=False, test_dashboard_live=False):
    """Compose browser transport with already bootstrapped Base writer authority.

    Only Base startup can supply the pinned identity and actual writer lease.
    BrowserOwnerAPI retains same-origin/session/CSRF as sole owner entry.
    """
    if (stage not in ('test','prod') or type(interactive_enabled) is not bool
            or type(node_writer_enabled) is not bool or type(test_dashboard_live) is not bool
            or (node_writer_enabled or test_dashboard_live) and stage!='test'):
        raise ValueError('explicit Base browser lane required')
    if not callable(verified_identity) or not callable(release_lease):
        raise ValueError('protected Base writer authority required')
    # Capture actual identity per request, not while the receiver may hold its
    # exclusive deployment lease while starting the process.
    def perform(action, args):
        if action not in _ACTIONS:
            raise ValueError('unsupported Base browser owner action')
        operations=owner_operations(database=config.db,release_lease=release_lease,
            identity=verified_identity,stage=stage,fresh_only=config.auto_process_fresh_mac,
            baseline=config.processing_session_ids)
        return operations(action,args)

    def project(*, limit, offset):
        with release_lease():
            return snapshot(config,identity=verified_identity,database=config.db,
                interactive_enabled=interactive_enabled,limit=limit,offset=offset,lane=stage,
                can_change=True,session_control=True,batch_cards=True,clear_ready=False)

    def node_writer(action,args):
        operations=owner_operations(database=config.db,release_lease=release_lease,
            identity=verified_identity,stage=stage,fresh_only=config.auto_process_fresh_mac,
            baseline=config.processing_session_ids)
        if action=='graph_node_preview' and args.get('expected_revision')=='0'*32:
            return operations(action,{k:v for k,v in args.items() if k!='expected_revision'})
        if action=='graph_node_remove':
            def authorize(_db):
                # Consume the HTTP proof handoff under the subject's write
                # transaction, which retains the existing history/audit.
                require_verified_base_commit(stage,args)
            return operations(action,args,authorize=authorize)
        raise ValueError('unsupported Base node owner action')

    browser=BrowserOwnerAPI(stage=stage,perform=perform,queue_snapshot=project,clear_enabled=False,dashboard_live=test_dashboard_live,
                            node_removal=node_writer if node_writer_enabled else None)
    if machine_devices is not None:
        browser.attach_machine_devices(machine_devices,release_lease,verified_identity)
    return browser
