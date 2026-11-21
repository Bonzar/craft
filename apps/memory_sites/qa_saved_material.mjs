/** Original reader pagination over already saved immutable synthetic material. Never generates text. */
const headers = {'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'};
const json = (body,status=200) => Response.json(body,{status,headers});
const error = (code,message,status=400) => json({error:{code,message}},status);
function integer(value, fallback, min, max) {
  if(value==null)return fallback;
  if(!/^[+-]?[0-9]+(?:_[0-9]+)*$/.test(value.trim()))return false;
  const result=Number(value.trim().replaceAll('_',''));
  return Number.isSafeInteger(result)&&result>=min&&result<=max ? result : null;
}
export function createQaSavedMaterialApi({manifest,topics,overviews=[],ownerSession}) {
  const map = new Map(), known = new Map(topics.topics.map(topic=>[topic.id,topic]));
  for(const overview of overviews) {
    const topic=known.get(overview.topic_id);
    if(!topic||map.has(topic.id)||overview.revision!==manifest.revision||overview.provider_id!==manifest.provider_id||
      overview.source_ref?.view_id!==topic.source_ref.view_id||overview.source_ref?.version_id!==topic.source_ref.version_id||
      overview.offset!==0||overview.next_offset!==null||typeof overview.text!=='string'||!overview.text_revision||
      overview.offset_unit!=='unicode_codepoints') throw new Error('Saved overview must match its immutable synthetic topic');
    const characters=Array.from(overview.text);
    if(characters.length!==overview.text_total) throw new Error('Saved overview is not complete');
    map.set(topic.id,{page:structuredClone(overview),characters});
  }
  return async request=>{
    const {pathname,searchParams}=new URL(request.url);
    const route=pathname.match(/^\/api\/v1\/overviews\/(.+)$/);
    if(!route)return null;
    const finish=r=>request.method==='HEAD'?new Response(null,{status:r.status,headers:r.headers}):r;
    if(!await ownerSession(request))return finish(error('unauthorized','Owner session required',401));
    if(!['GET','HEAD'].includes(request.method))return error('method_not_allowed','Use GET',405);
    const revision=searchParams.get('revision');
    if(!revision)return finish(error('revision_required','An immutable graph revision is required'));
    if(revision!==manifest.revision)return finish(error('revision_expired','Snapshot revision is unavailable; reload the graph',409));
    let id;try{id=decodeURIComponent(route[1]);}catch{}
    if(!known.has(id))return finish(error('not_found','Topic is unavailable in this perspective',404));
    const saved=map.get(id);
    if(!saved)return finish(error('overview_unavailable','Saved overview text is unavailable for this source version',404));
    const {page,characters}=saved;
    const offset=integer(searchParams.get('offset'),0,0,characters.length);
    const limit=integer(searchParams.get('limit'),12000,1,64000);
    if(offset===false||limit===false)return finish(error('invalid_integer','Invalid pagination value'));
    if(offset==null||limit==null)return finish(error('invalid_integer','Pagination value is outside allowed range'));
    const end=Math.min(offset+limit,characters.length);
    return finish(json({...page,text:characters.slice(offset,end).join(''),offset,next_offset:end<characters.length?end:null}));
  };
}
