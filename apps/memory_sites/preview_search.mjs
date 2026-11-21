/** Synthetic preview only: lexical graph nodes, not memory_query or semantic search.
 * Caller must supply trusted owner verification and the dedicated preview D1.
 * No application logging or writes; query strings may exist in platform logs.
 */
import {graphNodeLexical} from './graph_lexical.mjs';
const headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'};
const marker='private-synthetic-assertion-smoke/v1';
const json=(value,status=200)=>Response.json(value,{status,headers});

export function createPreviewSearch({owner}) {
  if(typeof owner!=='function') throw new TypeError('Trusted owner verifier required');
  return async (request,env) => {
    const url=new URL(request.url);
    if(url.pathname!=='/preview/search') return null;
    if(!await owner(request,env)) return json({error:'Owner required'},401);
    if(request.method!=='GET') return json({error:'Use GET'},405);
    const query=url.searchParams.get('q');
    if (url.searchParams.size!==1 || typeof query!=='string' || !query.trim() ||
        [...query].length>96 || /[\x00-\x1f\x7f]/u.test(query))
      return json({error:'Supply one synthetic keyword query, 1–96 characters'},400);
    try {
      const marked=await env.DB.prepare('SELECT purpose FROM memory_synthetic_smoke_environment WHERE singleton=1').first();
      if(marked?.purpose!==marker) return json({error:'Synthetic search unavailable'},503);
      const plan=graphNodeLexical({query,limit:8});
      if(!plan) return json({kind:'synthetic-graph-lexical',hits:[]});
      // Rename built-in table identifiers only in the fixed trusted SQL template.
      // The untrusted FTS expression stays in the bound arguments.
      const sql=plan.sql.replace(/\bsearch\b/g,'memory_synthetic_search_fts')
        .replace(/\bJOIN nodes\b/g,'JOIN memory_synthetic_search_nodes');
      const result=await env.DB.prepare(sql).bind(...plan.values).all();
      if(!Array.isArray(result?.results)) throw new Error('Search response unavailable');
      return json({kind:'synthetic-graph-lexical',hits:result.results.map(({id,text})=>({id,text}))});
    } catch {
      return json({error:'Synthetic search unavailable'},503);
    }
  };
}
