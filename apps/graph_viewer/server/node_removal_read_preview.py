"""Owner-only TEST eligibility preview; creates no confirmation or authority."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time

from prototypes.assertion_memory.graph_node_removal import NodeConflict, preview
from prototypes.assertion_memory.queue_rest import exact_database
from .common import APIError

REASONS=frozenset(('thought_changed_or_missing','thought_not_current_graph_node',
                   'dependent_thoughts','dependency_proof_unavailable','thought_work_in_progress'))


def preview_available(request):
    state=request.app.state
    dashboard=getattr(state,'queue_dashboard',None)
    owner_api=getattr(state,'browser_owner_api',None)
    source=getattr(state.manager.source,'source_path',None)
    return (request.method=='GET' and getattr(dashboard,'stage',None)=='test'
            and getattr(owner_api,'stage',None)=='test' and state.owner.session(request) is not None
            and source is not None and Path(dashboard.db)==Path(source))


def removal_preview(request,card):
    if not preview_available(request):
        raise APIError('not_found','Unknown node resource',404)
    dashboard=request.app.state.queue_dashboard
    if (card.get('is_current') is not True or card.get('session_context_only') is True
            or type(card.get('thought_ref')) is not dict):
        return dict(eligible=False, reason='thought_not_current_graph_node',
                    write_authority=False)
    ref=card['thought_ref']
    db_path=Path(dashboard.db)
    try:
        with dashboard.release_lease():
            physical=dashboard.activation()
            before=exact_database(db_path)
            with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True,timeout=.3)) as db:
                db.execute('PRAGMA query_only=ON')
                deadline=time.monotonic()+1
                db.set_progress_handler(lambda:time.monotonic()>=deadline,1000)
                db.execute('BEGIN')
                try:
                    preview(db,stage='test',binding=(before,physical),
                            thought_id=ref['thought_id'],thought_version=ref['version'])
                    reason=None
                except NodeConflict as exc:
                    reason=exc.code if exc.code in REASONS else 'preview_unavailable'
            if (dashboard.activation()!=physical or exact_database(db_path)!=before):
                raise ValueError('TEST preview identity changed')
    except (ValueError,OSError,sqlite3.Error,KeyError):
        raise APIError('preview_unavailable','Read-only node preview unavailable',503) from None
    return dict(eligible=reason is None,reason=reason,write_authority=False)
