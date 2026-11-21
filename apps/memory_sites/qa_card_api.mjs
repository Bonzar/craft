/** Read-only transport for prepared, immutable QA cards. Session callback is trusted. */
const CACHE_HEADERS = {'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'};
const PREFIX = '/api/v1';
const json = (value, status = 200) => Response.json(value,{status,headers:CACHE_HEADERS});
const error = (code,message,status = 400) => json({error:{code,message}},status);
const missing = kind => error('not_found',
  kind === 'nodes' ? 'Assertion is unavailable in this perspective' : 'Relationship is unavailable in this perspective',404);

export function createQaCardApi({ownerSession, loadRevision}) {
  return async request => {
    const {pathname,searchParams} = new URL(request.url);
    const session = pathname === PREFIX+'/session';
    const ordinal = pathname.match(/^\/api\/v1\/graph\/([^/]+)\/(nodes|edges)\/(\d+)$/);
    const stable = pathname.match(/^\/api\/v1\/(nodes|edges)\/(.+)$/);
    if (!session && !ordinal && !stable) return null;
    // Leave adjacency and all session mutations to their own routes.
    if ((session && request.method !== 'GET') || (stable?.[1] === 'nodes' && stable[2].endsWith('/edges'))) return null;
    const csrf = await ownerSession(request);
    if (session) return json(csrf ? {authenticated:true,csrf_token:csrf} : {authenticated:false});
    if (!csrf) return error('unauthorized','Owner session required',401);
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method Not Allowed',{status:405, headers:{...CACHE_HEADERS,Allow:'GET, HEAD'}});
    }
    const finish = r => request.method === 'HEAD' ? new Response(null,{status:r.status,headers:r.headers}) : r;
    const kind = ordinal?.[2] ?? stable[1];
    let revision, key;
    try {
      revision = ordinal ? decodeURIComponent(ordinal[1]) : searchParams.get('revision');
      key = ordinal ? Number(ordinal[3]) : decodeURIComponent(stable[2]);
    } catch { return finish(missing(kind)); }
    if (!revision) return finish(error('revision_required','An immutable graph revision is required'));
    const graph = await loadRevision(revision);
    if (!graph) return finish(error('revision_expired','Snapshot revision is unavailable; reload the graph',409));
    if (ordinal && !Number.isSafeInteger(key)) return finish(missing(kind));
    const value = await graph.card({kind,[ordinal?'ordinal':'id']:key,params:searchParams});
    return finish(value == null ? missing(kind) : json(value));
  };
}
