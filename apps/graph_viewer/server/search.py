"""Snapshot-filtered FTS keyword retrieval; semantic modes never silently fall back."""
import re
from urllib.parse import quote
from contextlib import closing
from .common import APIError,bounded,cursor_decode,cursor_encode,digest


def node_search_documents(text, metadata):
    canonical=metadata.get('canonical_thought')
    if not isinstance(canonical,dict):return [text]
    thought=canonical.get('thought')
    questions=thought.get('questions',[]) if isinstance(thought,dict) else canonical.get('questions',[])
    if not isinstance(questions,list) or any(not isinstance(value,str) for value in questions):return [text]
    return list(dict.fromkeys([text,*sorted(set(questions))]))


def node_search_text(text, metadata):
    return '\n'.join(node_search_documents(text,metadata))


def search(graph,params):
    mode=params.get('mode','keyword'); query=params.get('q','')
    if mode not in {'keyword','semantic'}:
        raise APIError('invalid_search_mode','Unknown search mode')
    if not query.strip() or len(query)>8000:
        raise APIError('invalid_query','Search requires between 1 and 8000 characters')
    if mode=='semantic':
        from .semantic import semantic_search
        return semantic_search(graph,params)
    limit=bounded(params.get('limit'),50,200,1)
    binding=f'search:{graph.revision}:{mode}:topics-v2:{digest(query)}'
    offset=cursor_decode(params.get('cursor'),binding)
    terms=re.findall(r'\w+',query,flags=re.UNICODE)
    if not terms:
        return {'revision':graph.revision,'mode':mode,'query':query,'hits':[],'topic_hits':[],'next_cursor':None,'index_revision':graph.revision}
    expression=' AND '.join('"'+term.replace('"','""')+'"' for term in terms)
    hidden=' AND n.flags&4=0'+('' if graph.manifest['selector']['history'] else ' AND n.flags&3=0')
    with closing(graph.connect()) as c:
        map_rows,map_count,topics=map_matches(c,graph,expression,limit+1,offset)
        entries=[]
        for row in map_rows:
            topic=topics[int(row['topic'])]
            routing=topic.get('purpose')=='routing_only'
            label=topic['label']
            prefix='Карта' if routing else 'Сохранённый вид'
            hit={'id':row['topic_id'],'topic_id':row['topic_id'],'topic_index':topic['index'],
                 'view_id':topic['source_ref']['view_id'],
                 'result_kind':'routing_map' if routing else 'saved_view',
                 'purpose':topic.get('purpose'),'label':label,'score':-row['score'],
                 'snippet':prefix+' «'+label+'»: '+snippet(row['text'],terms[0]),
                 'highlights':[],'member_count':topic['member_count'],
                 'overview_url':'/api/v1/overviews/'+quote(row['topic_id'],safe='')+'?revision='+quote(graph.revision,safe='')}
            entries.append(('topic',hit))
        remaining=limit+1-len(entries)
        if remaining:
            rows=c.execute('SELECT n.id,n.ordinal,n.text,bm25(search) AS score FROM search JOIN nodes n ON n.ordinal=search.rowid WHERE search MATCH ?'+hidden+' ORDER BY score,n.ordinal LIMIT ? OFFSET ?',
                           (expression,remaining,max(0,offset-map_count))).fetchall()
            entries.extend(('node',{'id':r['id'],'ordinal':r['ordinal'],'score':-r['score'],'snippet':snippet(r['text'],terms[0]),'highlights':[],'topic_ids':[]}) for r in rows)
    page=entries[:limit]
    return {'revision':graph.revision,'mode':mode,'query':query,
            'hits':[hit for kind,hit in page if kind=='node'],
            'topic_hits':[hit for kind,hit in page if kind=='topic'],
            'next_cursor':cursor_encode(binding,offset+limit) if len(entries)>limit else None,'index_revision':graph.revision}


def map_matches(c,graph,expression,limit,offset):
    """Search saved topic prose without impersonating an assertion member.

    The small map corpus is indexed in this connection's temporary database so
    immutable projection files and node FTS rowids remain untouched. Topic
    labels are display-only; stable questions and prose drive search.
    """
    topics={topic['index']:topic for topic in graph.topics()['topics']}
    c.execute('CREATE VIRTUAL TABLE temp.map_search USING fts5(question,text,topic UNINDEXED,topic_id UNINDEXED)')
    c.executemany('INSERT INTO map_search(rowid,question,text,topic,topic_id) VALUES(?,?,?,?,?)',
                  ((row['topic']+1,topics[row['topic']].get('query') or topics[row['topic']].get('definition_title') or '',
                    row['text'],row['topic'],row['id'])
                   for row in c.execute('SELECT topic,id,text FROM overviews ORDER BY topic')
                   if row['topic'] in topics and row['id']==topics[row['topic']]['id'] and (not graph.manifest['selector'].get('session_id') or topics[row['topic']].get('slice_member_count',0)>0 or topics[row['topic']].get('session_result'))))
    match=' FROM map_search WHERE map_search MATCH ?'
    count=c.execute('SELECT count(*)'+match,(expression,)).fetchone()[0]
    rows=c.execute('SELECT topic,topic_id,text,bm25(map_search) AS score'+match+' ORDER BY score,map_search.rowid LIMIT ? OFFSET ?',
                   (expression,limit,offset)).fetchall()
    return rows,count,topics


def snippet(text,term):
    match=text.casefold().find(term.casefold())
    start=max(0,match-100)
    return ('…' if start else '')+text[start:start+420]+('…' if start+420<len(text) else '')
