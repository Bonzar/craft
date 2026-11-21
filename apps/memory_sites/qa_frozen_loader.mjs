import { createQaRevisionApi } from './qa_revision_api.mjs';
import { createQaCardApi } from './qa_card_api.mjs';

/** Local synthetic-only adapter. A real deployment needs trusted owner auth and storage. */
export function frozenQaApi({manifest, capabilities, cards, artifacts, topics}, ownerSession, {rawTransport = false} = {}) {
  const snapshot = structuredClone(manifest);
  const selectors = snapshot.selector;
  if (selectors.mode !== 'current' || selectors.undated !== 'separate' ||
      selectors.session_id != null || !snapshot.revision || capabilities.provider_id !== snapshot.provider_id) {
    throw new Error('Unexpected frozen synthetic snapshot');
  }
  const map = new Map();
  for (const {kind, card} of cards) {
    if (!['nodes','edges'].includes(kind) || card.revision !== snapshot.revision) throw new Error('Card revision conflict');
    for (const [type, value] of [['ordinal',card.ordinal],['id',card.id]]) {
      const key = JSON.stringify([kind,type,value]);
      if (map.has(key)) throw new Error('Duplicate card key');
      map.set(key,structuredClone(card));
    }
  }
  if (topics && (topics.revision!==snapshot.revision || !Array.isArray(topics.topics) || topics.topics.length!==snapshot.counts.topics)) {
    throw new Error('Unexpected frozen synthetic topic revision');
  }
  const payload = new Map();
  for (const kind of ['nodes','edges']) {
    const name=kind+'.bin';
    for (const encoding of ['gzip','identity']) {
      const raw=artifacts[name]?.[encoding];
      if (!(raw instanceof Uint8Array)) throw new Error('Missing frozen binary');
      payload.set(name+':'+encoding,new Uint8Array(raw));
    }
  }
  const graph = {
    artifact: (name, gzip) => payload.get(name+':'+(gzip?'gzip':'identity')),
    card: ({kind,id,ordinal}) => map.get(JSON.stringify([kind,id==null?'ordinal':'id',id??ordinal]))
  };
  const loadRevision = revision => revision===snapshot.revision ? graph : null;
  const status = {...capabilities,capabilities:{...capabilities.capabilities,
    live:false,history:false,topics:!!topics&&capabilities.capabilities.topics,topic_hierarchy:!!topics&&capabilities.capabilities.topic_hierarchy,source_context:false,
    whole_saved_source:false,keyword_search:false,semantic_search:false,session_filter:false}};
  const routeRevision=createQaRevisionApi({authorize:async r=>Boolean(await ownerSession(r)),loadRevision,
    loadCapabilities:()=>status,rawTransport,loadCurrent: selector=>selector.history===selectors.history ? snapshot : null});
  const routeCard=createQaCardApi({ownerSession,loadRevision});
  return async request => {
    const url=new URL(request.url);
    if (url.pathname===snapshot.topics_url && topics) {
      const headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'};
      if (!await ownerSession(request)) return Response.json({error:{code:'unauthorized',message:'Authentication required'}},{status:401,headers});
      if (request.method!=='GET') return Response.json({error:{code:'method_not_allowed',message:'Use GET'}},{status:405,headers});
      return Response.json(topics,{headers});
    }
    if (url.pathname==='/api/v1/graph') {
      const history=['1','true'].includes(url.searchParams.get('history'));
      if (history!==selectors.history && await ownerSession(request)) {
        return Response.json({error:{code:'selector_not_supported',message:'This snapshot selector is not available'}},
          {status:501,headers:{'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}});
      }
    }
    return (await routeRevision(request)) ?? (await routeCard(request));
  };
}
