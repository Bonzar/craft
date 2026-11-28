"""Source-only composition specimen; never loads settings or starts a server."""
from dataclasses import dataclass
from pathlib import Path
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.queue_rest import snapshot
from apps.graph_viewer.server.graph_node_owner_api import native_node_writer
from . import graph_node_removal as nodes, queue_item_removal as items
from .processing_control import Conflict

_ACTIONS = frozenset((
    'pause_prepare', 'pause_set', 'sessions_prepare', 'sessions_set',
    'policy_prepare', 'policy_set', 'item_preview', 'item_remove',
))

@dataclass(frozen=True)
class NativeOwnerAuthority:
    database: Path
    ready: object
    clear: bool
    batch: bool
    node: bool

    @classmethod
    def from_private(cls, controller, database):
        ready=getattr(controller,'queue_pause_ready',None)
        if not callable(ready) or getattr(controller,'queue_clear_database',None)!=database:
            return None
        routes=getattr(controller,'queue_item_routes',None)
        registered=lambda path: type(routes) is dict and callable(routes.get(path))
        return cls(database,ready,callable(getattr(controller,'queue_clear_route',None)),
                   registered(items.PATH),registered(nodes.PATH) and registered(nodes.PREVIEW_PATH))


def base_owner_browser(*, stage, config, verified_identity, release_lease, interactive_enabled, machine_devices=None, private_owner_queue=None,
                       authority=None, scheduler_config=None):
    """Compose browser transport with already bootstrapped Base writer authority.

    Only Base startup can supply the pinned identity and actual writer lease.
    BrowserOwnerAPI retains same-origin/session/CSRF as sole owner entry.
    """
    if stage not in ('test','prod') or type(interactive_enabled) is not bool:
        raise ValueError('explicit Base browser lane required')
    if not callable(verified_identity) or not callable(release_lease):
        raise ValueError('protected Base writer authority required')
    # Only a handler already bound by Base startup can carry this grant;
    # never accept browser-supplied flags, credentials, or a second DB.
    database=Path(config.db)
    if authority is None:
        authority=NativeOwnerAuthority.from_private(private_owner_queue,database)
    elif (type(authority) is not NativeOwnerAuthority or authority.database!=database
            or not callable(authority.ready) or private_owner_queue is not None):
        raise ValueError('Expected exact bound owner writer authority')
    clear_granted=authority is not None and authority.clear
    batch_granted=authority is not None and authority.batch
    node_granted=authority is not None and authority.node
    scheduler_config=scheduler_config or (lambda:config)

    def writer_ready():
        try:
            return authority.ready() is True
        except (ValueError,OSError):
            return False

    def require_current(_db=None):
        if not writer_ready():
            raise Conflict('Current Base owner writer authority not ready')

    # Capture actual identity per request, not while the receiver may hold its
    # exclusive deployment lease while starting the process.
    def perform(action, args):
        clear_action=action in ('clear_prepare','clear_commit')
        batch_action=(action=='item_remove' and args.get('queue_id')=='source'
                      and args.get('item_id','').startswith('batch:'))
        if batch_action and not batch_granted:
            raise Conflict('Current Base batch writer authority not ready')
        if action not in _ACTIONS and not (clear_action and clear_granted):
            raise ValueError('unsupported Base browser owner action')
        active=scheduler_config()
        operations=owner_operations(database=database,release_lease=release_lease,
            identity=verified_identity,stage=stage,fresh_only=active.auto_process_fresh_mac,
            baseline=active.processing_session_ids)
        if clear_action or batch_action:
            require_current()
            return operations(action,args,authorize=require_current)
        return operations(action,args)

    def project(*, limit, offset):
        with release_lease():
            active=scheduler_config()
            return snapshot(active,identity=verified_identity,database=database,
                interactive_enabled=getattr(active,'interactive_enabled',interactive_enabled),
                limit=limit,offset=offset,lane=stage,can_change=True,
                session_control=active.processing_session_ids is not None or active.auto_process_fresh_mac,
                batch_cards=True,clear_ready=False,
                batch_remove=batch_granted and writer_ready())

    node_writer=native_node_writer(stage=stage,database=database,identity=verified_identity,
        release_lease=release_lease,require_current=require_current) if node_granted else None
    browser=BrowserOwnerAPI(stage=stage,perform=perform,queue_snapshot=project,clear_enabled=clear_granted,
        dashboard_live=False,single_clear=clear_granted,clear_ready=writer_ready if clear_granted else None,
        node_removal=node_writer,node_removal_ready=writer_ready if node_granted else None)
    if machine_devices is not None:
        browser.attach_machine_devices(machine_devices,release_lease,verified_identity)
    return browser
