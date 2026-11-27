// IndexedDB contains one scoped topology checkpoint, never cards, raw text,
// session cookies/CSRF, SSE ticket, query text, or checksummed wire fragments.
const EPOCH=/^[a-f0-9]{64}$/, REV=/^[a-f0-9]{32}$/, CURSOR=/^(0|[1-9][0-9]{0,19})$/;
const DB='private-owner-graph-topology-v1', STORE='checkpoint';
const FIELDS=['mode','at','history','undated','session_id','run_id','output_snapshot_id'];
const MAX_NODES=2_000_000,MAX_EDGES=4_000_000;
export function ownerGraphCheckpointKey({origin,owner,provider,selector}){
  if(typeof origin!=='string'||typeof owner!=='string'||!EPOCH.test(owner)||
      typeof provider!=='string'||!provider||provider.length>160||!selector)return null;
  let u;try{u=new URL(origin);}catch{return null;}
  if(u.origin!==origin||u.protocol!=='https:')return null;
  const shape=FIELDS.map(field=>selector[field]??null);
  if(shape.some(x=>x!==null&&typeof x!=='boolean'&&typeof x!=='number'&&(typeof x!=='string'||x.length>200)))return null;
  return JSON.stringify([1,origin,owner,provider,shape]);
}

export function copyOwnerTopology({epoch,revision,cursor,ids,graph,visualPositions,view}){
  if(typeof epoch!=='string'||!EPOCH.test(epoch)||typeof revision!=='string'||!REV.test(revision)||
      typeof cursor!=='string'||!CURSOR.test(cursor)||!Array.isArray(ids)||new Set(ids.filter(x=>x!==null)).size!==ids.filter(x=>x!==null).length||
      ids.length>MAX_NODES||ids.some(x=>x!==null&&(typeof x!=='string'||x.length<1||x.length>1024))||
      !(graph?.positions instanceof Float32Array)||graph.positions.length!==ids.length*3||
      !(graph.flags instanceof Uint32Array)||graph.flags.length!==ids.length||
      !(graph.edges instanceof Uint32Array)||graph.edges.length%2||graph.edges.length>MAX_EDGES*2||
      !(graph.edgeActive instanceof Uint8Array)||graph.edgeActive.length*2!==graph.edges.length||
      !(visualPositions instanceof Float32Array)||visualPositions.length!==ids.length*3||
      graph.positions.some(x=>!Number.isFinite(x))||visualPositions.some(x=>!Number.isFinite(x))||
      graph.edges.some(x=>x>=ids.length)||graph.edgeActive.some(x=>x!==0&&x!==1))return null;
  return {epoch,revision,cursor,ids:ids.slice(),graph:{positions:graph.positions.slice(),flags:graph.flags.slice(),edges:graph.edges.slice(),edgeActive:graph.edgeActive.slice()},visualPositions:visualPositions.slice(),...(view?{view:ownerGraphViewMetadata(view.manifest,view.topics)}:{})};
}

// Only one accepted scope is retained; on identity/scope mismatch delete it in
// the same read-write transaction before returning cold. Database failures
// never prevent a cold authenticated connection.
export function ownerGraphCache(store){
  return {
    async take(scope){
      if(!scope||!store)return null;
      try{
        return await store.change(current=>{
          if(!current)return {result:null};
          if(current.scope!==scope)return {delete:true,result:null};
          const value=copyOwnerTopology(current.value??{});
          return value?{result:value}:{delete:true,result:null};
        });
      }catch{return null;}
    },
    async save(scope,input){
      if(!scope||!store)return false;
      try{
        const value=copyOwnerTopology(input);
        if(!value)return false;
        return await store.change(current=>{
          if(current?.scope===scope&&current.value?.epoch===value.epoch&&BigInt(current.value.cursor)>BigInt(value.cursor))
            return {result:false};
          return {put:{scope,value},result:true};
        });
      }catch{return false;}
    },
    async clear(){if(store)try{await store.change(()=>({delete:true,result:true}));}catch{}},
  };
}

export function browserOwnerTopologyStore(factory){
  if(arguments.length===0)try{factory=globalThis.indexedDB;}catch{return null;}
  if(!factory)return null;
  return {change(fn){
    return new Promise((resolve,reject)=>{
      let db,txn,settled=false;
      const timer=setTimeout(()=>{try{txn?.abort();}catch{}finish(new Error('Local checkpoint storage unavailable'));},300);
      function finish(error,result){
        if(settled)return;
        settled=true;clearTimeout(timer);
        db?.close();
        if(error)reject(error);else resolve(result);
      }
      const open=factory.open(DB,1);
      open.onupgradeneeded=()=>open.result.createObjectStore(STORE);
      open.onerror=()=>finish(open.error);
      open.onblocked=()=>finish(new Error('Local checkpoint storage blocked'));
      open.onsuccess=()=>{
        db=open.result;
        if(settled){db.close();return;}
        try{
          txn=db.transaction(STORE,'readwrite');
          const store=txn.objectStore(STORE),read=store.get('latest');
          let result;
          txn.oncomplete=()=>finish(null,result);
          txn.onerror=txn.onabort=()=>finish(txn.error??new Error('Local checkpoint transaction aborted'));
          read.onsuccess=()=>{
            if(settled){txn.abort();return;}
            try{
              const changed=fn(read.result);
              result=changed.result;
              if(changed.delete)store.delete('latest');
              if(changed.put)store.put(changed.put,'latest');
            }catch(error){txn.abort();finish(error);}
          };
        }catch(error){finish(error);}
      };
    });
  }};
}

export function ownerGraphViewMetadata(manifest,topics){
  if(!manifest||!topics||manifest.schema!==1||!manifest.complete_graph||topics.revision!==manifest.revision)return null;
  const pick=(value,keys)=>Object.fromEntries(keys.filter(k=>Object.hasOwn(value??{},k)).map(k=>[k,value[k]]));
  const selector=pick(manifest.selector,FIELDS);
  // Historical result titles/provenance are not cached. History still streams
  // in full on this same connection when we cannot reconstruct it safely.
  if(selector.session_id!==null&&selector.session_id!==undefined)return null;
  const m=pick(manifest,['schema','revision','source_revision','provider_id','owner_live_epoch','captured_at',
    'topics_url','complete_graph','live_cursor','status_at','knowledge_cutoff']);
  m.selector=selector;
  m.counts=pick(manifest.counts,['nodes','edges','visible_nodes','visible_edges','topics','eligible_before_status_filter',
    'undated_known_count','excluded_unknown_valid_count','future_valid_known_count','source_eligible_edges']);
  for(const name of ['nodes','edges'])m[name]=pick(manifest[name],['url','format','stride','byte_length']);
  m.bounds={min:[...manifest.bounds.min],max:[...manifest.bounds.max]};
  m.layout=pick(manifest.layout,['version','semantic','stable']);
  m.delta_capabilities=(manifest.delta_capabilities??[]).filter(s=>s==='source-membership/v1');
  m.session_filter=null;
  const topic=t=>({...pick(t,['id','index','radius','depth','member_count','available_at']),
    source_ref:pick(t.source_ref,['view_id','version_id','route_revision']),position:[...t.position],
    label:'Тема',full_title:'Тема',label_method:'topology-only',freshness:'unknown',purpose:null,overview_protocol:null});
  const memberships=a=>a.map(t=>({topic_index:t.topic_index,node_indices:[...t.node_indices]}));
  const projection={revision:topics.revision,topics:topics.topics.map(topic),memberships:memberships(topics.memberships),
    layout_topics:topics.layout_topics?.map(topic)??null,layout_memberships:topics.layout_memberships?memberships(topics.layout_memberships):null,
    links:topics.links.map(t=>({parent:t.parent,child:t.child,relation:'topology',provenance:null})),unknowns:[]};
  return {manifest:m,topics:projection};
}
