"""Visual metadata refresh over immutable canonical pages; no graph reindex."""
import json
from .providers.assertion_memory_names import capture_names,resolve_name

PRESENTATION_POLICY='exact-material-title-v2'

def policy_current(manifest):
    return manifest.get('preparation',{}).get('presentation_policy')==PRESENTATION_POLICY

def semantic_marks(marks):
    result={k:v for k,v in marks.items() if k!='presentation'}
    if 'topics' in result:
        result['topics']=[row for row in json.loads(result['topics']) if row[0]!='memory_view_names']
    return json.dumps(result,sort_keys=True)

def refresh_labels(origin,topics,selection,captured,watermark,keys=None):
    tables={row[0] for row in origin.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cutoff=selection['at'] if selection['mode']=='known_then' else None
    snapshot=capture_names(origin,tables,snapshot={'presentation_cutoff':captured,'name_watermark':watermark},recorded_as_of=cutoff)
    changed=[]
    for topic in topics['topics']:
        key=topic['source_ref']['view_id']
        if keys is not None and key not in keys:continue
        definition=json.loads(origin.execute('SELECT definition FROM memory_views WHERE id=?',(key,)).fetchone()[0])
        name=resolve_name(origin,tables,key,definition,snapshot,version_id=topic['source_ref']['version_id'],allow_legacy=selection['mode']!='current')
        title=name.get('display_title') or definition.get('query') or definition.get('title') or ''
        topic.update(name,label=title,full_title=title,
            label_method='source_editorial_name' if name['naming_status']=='accepted' else 'source_question' if name['naming_status']=='naming_pending' else 'source_title_unreviewed')
        changed.append(dict(topic))
    topics['presentation_snapshot']=snapshot
    return changed

def inherit_semantic(graph,parent):
    """Share exact same node-vector matrix; an in-progress owner finishes once."""
    from .incremental import clone_file
    graph.semantic_parent=getattr(parent,'semantic_parent',parent)
    if not hasattr(parent,'semantic_matrix'):return
    for name in ('vectors.f32','semantic.json'):
        source=parent.directory/name
        if source.is_file():clone_file(source,graph.directory/name);(graph.directory/name).chmod(0o600)
    graph.semantic_model=parent.semantic_model;graph.semantic_matrix=parent.semantic_matrix
    graph.semantic_info=dict(parent.semantic_info,presentation_reused=True)
    graph.manifest['preparation']['semantic']=graph.semantic_info
