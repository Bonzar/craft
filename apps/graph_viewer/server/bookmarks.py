"""Stable bookmarks are resolved against their provider and saved perspective."""
import json
from .common import APIError


async def resolve_bookmarks(manager,result):
    for item in result['items']:
        if item['provider_id']!=manager.source.provider_id or not manager.available:
            item['availability']='provider_unavailable'
            continue
        selection=item.get('selector') or {'mode':'current','at':None,'history':False,'undated':'separate'}
        # Extra UI date/timezone values are preserved in bookmarks, not graph selectors.
        selection={key:selection[key] for key in ['mode','at','history','undated','session_id','run_id','output_snapshot_id'] if key in selection}
        try:
            graph=await manager.ensure(selection)
        except APIError as error:
            if error.code not in ('session_unavailable','run_unavailable','output_snapshot_unavailable','causal_history_unavailable'):raise
            item['availability']='hidden_in_slice';continue
        exists=manager.source.has_object(item['object_kind'],item['object_id'])
        if not exists:
            item['availability']='missing'
        elif graph is None:
            item['availability']='available'
        elif item['object_kind']=='topic':
            item['availability']='available' if any(t['id']==item['object_id'] and (not selection.get('session_id') or t.get('slice_member_count',0)>0 or t.get('session_result')) for t in graph.topics()['topics']) else 'hidden_in_slice'
        else:
            with graph.connect() as c:
                row=c.execute('SELECT flags FROM nodes WHERE id=?',(item['object_id'],)).fetchone()
            item['availability']='hidden_in_slice' if row is None or row[0]&4 or (not selection['history'] and row[0]&3) else 'available'
    return result
