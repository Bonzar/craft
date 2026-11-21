/** Owner-authorized callers only. No raw body or model work. Canonical D1 raw captures. */
const meta=k=>`json_extract(c.raw_json,'$.metadata.${k}')`;
const native=`CASE WHEN json_type(c.raw_json,'$.metadata.native_session_id')='text' AND length(trim(${meta('native_session_id')}))>0 THEN ${meta('native_session_id')} END`;
const source=`coalesce((${native}),c.session_id)`;
const timestamp="json_extract(c.raw_json,'$.recorded_at')";
const captureTurn="json_extract(c.raw_json,'$.turn_id')";
const basis=(count,total)=>count===total?'metadata.native_session_id':!count?'stored_session_id_fallback':'mixed_native_and_stored_fallback';
const id=(value,name)=>{
  if(typeof value!=='string'||!value.trim()||Array.from(value).length>2048)
    throw new TypeError(name+' must be a nonempty ID of at most 2048 characters');
  return value;
};
const bind=(db,sql,params)=>params.length?db.prepare(sql).bind(...params):db.prepare(sql);
const rows=r=>{
  if(r?.success===false||!Array.isArray(r?.results))throw new Error('Could not read captured sessions.');
  return r.results;
};
// Python json.dumps defaults add one space after each comma/colon outside strings.
function encodedLength(v){
  if(Array.isArray(v))return 2+v.map(encodedLength).reduce((a,b)=>a+b,0)+2*Math.max(0,v.length-1);
  if(v!==null&&typeof v==='object'){
    const entries=Object.entries(v);
    return 2+entries.reduce((a,[k,v])=>a+encodedLength(k)+2+encodedLength(v),0)+2*Math.max(0,entries.length-1);
  }
  return Array.from(JSON.stringify(v)).length;
}
function envelope(key,items,total,offset,snapshot){
  while(items.length&&encodedLength(items)>22000)items.pop();
  if(!items.length&&offset<total)throw new RangeError('source identity exceeds response budget');
  const next=offset+items.length;
  return {[key]:items,total,offset,next_offset:next<total?next:null,snapshot_id:snapshot,
    count_basis:'stored_raw_events',response_complete:next>=total};
}
export function createRawSessionReads(db){
  async function page({offset=0,limit=50,snapshot_id=null}){
    if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1||limit>200)
      throw new RangeError('offset must be nonnegative and limit between 1 and 200');
    if(snapshot_id!=null&&(!Number.isSafeInteger(snapshot_id)||snapshot_id<0))throw new RangeError('snapshot_id must be nonnegative');
    const snapshot=snapshot_id ?? rows(await db.prepare('SELECT coalesce(max(rowid),0) AS snapshot FROM memory_raw_captures').all())[0].snapshot;
    return {offset,limit,snapshot};
  }
  return {
    async memory_sessions(options={}){
      const {offset,limit,snapshot}=await page(options);
      const [count,result]=await db.batch([
        bind(db,`SELECT count(DISTINCT ${source}) AS total FROM memory_raw_captures c WHERE c.rowid<=?`,[snapshot]),
        bind(db,`SELECT ${source} AS session_id,count(*) AS message_count,min(${timestamp}) AS first_recorded_at,
          max(${timestamp}) AS last_recorded_at,count(DISTINCT c.session_id) AS capture_session_count,
          CASE WHEN count(DISTINCT c.session_id)=1 THEN min(c.session_id) END AS capture_session_id,
          sum((${native}) IS NOT NULL) AS native_message_count FROM memory_raw_captures c WHERE c.rowid<=?
          GROUP BY ${source} ORDER BY session_id LIMIT ? OFFSET ?`,[snapshot,limit,offset]),
      ]);
      const items=rows(result).map(row=>({...row,source_session_id:row.session_id,
        native_session_id:row.native_message_count?row.session_id:null,
        fallback_message_count:row.message_count-row.native_message_count,
        session_id_basis:basis(row.native_message_count,row.message_count),title:null,title_source:null}));
      return {...envelope('sessions',items,rows(count)[0].total,offset,snapshot),
        session_identity_basis:'explicit_native_session_or_stored_fallback'};
    },
    async memory_session_messages(options={}){
      const {session_id,turn_id=null,message_id=null}=options;
      id(session_id,'session_id');
      const {offset,limit,snapshot}=await page(options);
      let where=`c.rowid<=? AND (${source}=? OR c.session_id=?)`;
      const args=[snapshot,session_id,session_id];
      if(turn_id!==null){where+=` AND ${captureTurn}=?`;args.push(id(turn_id,'turn_id'))}
      if(message_id!==null){where+=` AND ${meta('message_id')}=?`;args.push(id(message_id,'message_id'))}
      const [count,result]=await db.batch([
        bind(db,`SELECT count(*) AS total FROM memory_raw_captures c WHERE ${where}`,args),
        bind(db,`SELECT c.event_id,${source} AS session_id,c.session_id AS capture_session_id,${native} AS native_session_id,
          ${captureTurn} AS turn_id,${meta('native_turn_id')} AS native_turn_id,${meta('message_id')} AS message_id,
          ${meta('role')} AS role,json_extract(c.raw_json,'$.actor') AS actor,${timestamp} AS recorded_at,
          length(json_extract(c.raw_json,'$.prompt')) AS text_characters,c.position FROM memory_raw_captures c WHERE ${where}
          ORDER BY coalesce(c.position,c.rowid),c.rowid LIMIT ? OFFSET ?`,[...args,limit,offset]),
      ]);
      const items=rows(result).map(row=>({...row,source_session_id:row.session_id,
        session_id_basis:basis(Number(row.native_session_id!==null),1),turn_id_basis:'stored_capture_turn_id'}));
      return {...envelope('messages',items,rows(count)[0].total,offset,snapshot),session_id,
        lookup_basis:'exact_source_or_capture_session_id',
        notice:'session_id/source_session_id use explicit native identity or stored fallback; capture_session_id is the logical intake group. A capture-group lookup may include several source chats. turn_id is the stored capture identity; native_turn_id is explicit native provenance or null. Read bodies with memory_source(event_id).'};
    },
  };
}
