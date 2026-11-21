import { createQaRevisionApi } from './qa_revision_api.mjs';
import { createQaCardApi } from './qa_card_api.mjs';

/** Local synthetic-only adapter. A real deployment needs trusted owner auth and storage. */
export function frozenQaApi({manifest, capabilities, cards, artifacts}, ownerSession) {
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
    live:false,history:false,topics:false,topic_hierarchy:false,source_context:false,
    whole_saved_source:false,keyword_search:false,semantic_search:false,session_filter:false}};
  const routeRevision=createQaRevisionApi({authorize:async r=>Boolean(await ownerSession(r)),loadRevision,
    loadCapabilities:()=>status,loadCurrent: selector=>selector.history===selectors.history ? snapshot : null});
  const routeCard=createQaCardApi({ownerSession,loadRevision});
  return async request => {
    const url=new URL(request.url);
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
