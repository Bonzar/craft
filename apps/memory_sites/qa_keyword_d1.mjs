// Dedicated preview-only index for one frozen revision. Never query v1 demo or real data.
const aliases={nodes:'qa_kw_nodes',search:'qa_kw_fts',map_search:'qa_kw_overviews_fts'};
const rows = r=>{
  if(r?.success===false||!Array.isArray(r?.results))throw Error('QA keyword index failed');
  return r.results;
};
const unavailable=()=>Response.json({error:{code:'keyword_unavailable',message:'The keyword index for this revision is not ready'}},
  {status:503,headers:{'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}});
export {unavailable as qaKeywordUnavailable};

export function createQaKeywordD1(snapshot) {
  const {manifest,topics,keyword_index:index}=snapshot;
  if(!index)return async()=>null;
  const map=new Map(topics.topics.map(t=>[t.index,t]));
  if(index.revision!==manifest.revision||index.nodes!==manifest.counts.nodes||
      !Number.isSafeInteger(index.maps)||index.maps<0||index.maps>map.size||
      !/^[a-f0-9]{64}$/.test(index.sha256))throw Error('Invalid frozen keyword index metadata');
  return async db=>{
    if(!db)return null;
    try{
      const row=rows(await db.prepare(`SELECT m.revision,m.sha256,
        (SELECT count(*) FROM qa_kw_nodes) AS nodes,(SELECT count(*) FROM qa_kw_fts) AS hits,
        (SELECT count(*) FROM qa_kw_overviews_fts) AS maps
        FROM qa_kw_marker m WHERE m.id=1`).all())[0];
      if(!row||row.revision!==index.revision||row.sha256!==index.sha256||
        row.nodes!==index.nodes||row.hits!==index.nodes||row.maps!==index.maps)return null;
    }catch{return null;}
    // SQL strings are authored in the internal keyword adapter only; queries are bound parameters.
    return {history:manifest.selector.history,topics:map,async all(sql,args){
      // Keep marker and row retrieval in one transaction so a new projection
      // cannot answer a request after an older revision has been admitted.
      const checks=db.prepare('SELECT revision,sha256 FROM qa_kw_marker WHERE id=1');
      const data=db.prepare(sql.replace(/\b(nodes|search|map_search)\b/g,key=>aliases[key])).bind(...args);
      const [identity,result]=await db.batch([checks,data]);
      const head=rows(identity)[0];
      if(head?.revision!==index.revision||head.sha256!==index.sha256)throw Error('QA keyword revision changed');
      return rows(result);
    }};
  };
}
