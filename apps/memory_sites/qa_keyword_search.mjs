/** Keyword results for a single immutable graph projection, not MCP memory_query. */
import {graphNodeLexical} from './graph_lexical.mjs';
import {casefold} from './qa_casefold.mjs';
const encoder=new TextEncoder();
const path='/api/v1/search';
const headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'};
const json=(body,status=200)=>Response.json(body,{status,headers});
const error=(code,message,status=400)=>json({error:{code,message}},status);
const integer=(value,otherwise,min,max)=>{
  if(value===null)return otherwise;
  if(!/^[+-]?[0-9]+(?:_[0-9]+)*$/.test(value.trim()))throw 0;
  const v=Number(value.trim().replaceAll('_',''));if(v<min||v>max)throw 1;return v;
};
const cursor=(binding,offset)=>btoa(`[${JSON.stringify(binding)}, ${offset}]`).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
const digest=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(text))),x=>x.toString(16).padStart(2,'0')).join('');
function snippet(text,term){
  const match=casefold(text).indexOf(casefold(term));
  const offset=match<0?0:Array.from(casefold(text).slice(0,match)).length;
  const start=Math.max(0,offset-100), characters=Array.from(text);
  return (start?'…':'')+characters.slice(start,start+420).join('')+(start+420<characters.length?'…':'');
}
const topicsQuery=' FROM map_search WHERE map_search MATCH ?';
const first=(params,key)=>params.getAll(key).at(-1)??null; // Starlette query params are last-wins.
/** Projection DB must contain dedicated, complete per-revision nodes/search/map_search rows. */
export function createQaKeywordSearch({authorize,loadRevision}){
  return async request=>{
    const {pathname,searchParams}=new URL(request.url);
    if(pathname!==path)return null;
    if(!await authorize(request))return error('unauthorized','Owner session required',401);
    if(request.method!=='GET')return error('method_not_allowed','Use GET',405);
    const revision=first(searchParams,'revision');
    if(!revision)return error('revision_required','An immutable graph revision is required');
    const graph=await loadRevision(revision);
    if(!graph)return error('revision_expired','Snapshot revision is unavailable; reload the graph',409);
    const mode=first(searchParams,'mode')??'keyword',query=first(searchParams,'q')??'';
    if(!['keyword','semantic'].includes(mode))return error('invalid_search_mode','Unknown search mode');
    if(!query.trim()||Array.from(query).length>8000)return error('invalid_query','Search requires between 1 and 8000 characters');
    if(mode==='semantic')return error('semantic_unavailable','Semantic query model or complete index is unavailable',503);
    let limit;try{limit=integer(first(searchParams,'limit'),50,1,200)}catch(e){return error('invalid_integer',e===0?'Invalid pagination value':'Pagination value is outside allowed range')}
    const binding=`search:${revision}:${mode}:topics-v2:${await digest(query)}`;
    let offset=0;
    const rawCursor=first(searchParams,'cursor');
    if(rawCursor){
      try{
        const [key,n]=JSON.parse(atob(rawCursor.replaceAll('-','+').replaceAll('_','/')));
        if(key!==binding||!Number.isSafeInteger(n)||n<0)throw 0;offset=n;
      }catch{return error('invalid_cursor','Cursor does not belong to this revision and query')}
    }
    const terms=query.match(/[\p{L}\p{N}_]+/gu)??[];
    const out={revision,mode,query,hits:[],topic_hits:[],next_cursor:null,index_revision:revision};
    if(!terms.length)return json(out);
    const expression=terms.map(term=>`"${term}"`).join(' AND ');
    const count=(await graph.all('SELECT count(*) AS cnt'+topicsQuery,[expression]))[0].cnt;
    const maps=await graph.all('SELECT topic,topic_id,text,bm25(map_search) AS score'+topicsQuery+' ORDER BY score,map_search.rowid LIMIT ? OFFSET ?',[expression,limit+1,offset]);
    const entries=maps.map(row=>{
      const topic=graph.topics.get(row.topic),routing=topic.purpose==='routing_only',label=topic.label;
      return ['topic',{id:row.topic_id,topic_id:row.topic_id,topic_index:topic.index,view_id:topic.source_ref.view_id,
        result_kind:routing?'routing_map':'saved_view',purpose:topic.purpose??null,label,score:-row.score,
        snippet:`${routing?'Карта':'Сохранённый вид'} «${label}»: ${snippet(row.text,terms[0])}`,highlights:[],
        member_count:topic.member_count,overview_url:`/api/v1/overviews/${encodeURIComponent(row.topic_id).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase())}?revision=${revision}`}];
    });
    const remaining=limit+1-entries.length;
    if(remaining){
      const page=graphNodeLexical({query,history:graph.history,limit:Math.min(remaining,200),offset:Math.max(0,offset-count)});
      page.values[1]=remaining; // one look-ahead row at the public 200 limit
      for(const row of await graph.all(page.sql,page.values))entries.push(['node',{id:row.id,ordinal:row.ordinal,score:-row.score,
        snippet:snippet(row.text,terms[0]),highlights:[],topic_ids:[]}]);
    }
    for(const [type,data] of entries.slice(0,limit))out[type==='node'?'hits':'topic_hits'].push(data);
    if(entries.length>limit)out.next_cursor=cursor(binding,offset+limit);
    return json(out);
  };
}
