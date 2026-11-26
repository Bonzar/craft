"""Source-only composition specimen; never loads settings or starts a server."""
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.queue_rest import snapshot

_ACTIONS = frozenset((
    'pause_prepare', 'pause_set', 'sessions_prepare', 'sessions_set',
    'policy_prepare', 'policy_set', 'item_preview', 'item_remove',
))

def base_owner_browser(*, stage, config, verified_identity, release_lease, interactive_enabled):
    """Compose browser transport with already bootstrapped Base writer authority.

    Only Base startup can supply the pinned identity and actual writer lease.
    BrowserOwnerAPI retains same-origin/session/CSRF as sole owner entry.
    """
    if stage not in ('test','prod') or type(interactive_enabled) is not bool:
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

    return BrowserOwnerAPI(stage=stage,perform=perform,queue_snapshot=project,clear_enabled=False)
