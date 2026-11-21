/** Frozen prepared-revision transport. Integrator provides trusted auth and data. */
const PREFIX = '/api/v1';
const files = new Set(['nodes.bin', 'edges.bin']);
const privacy = {'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff'};
function json(value, status = 200, headers = {}) {
  return Response.json(value, {status, headers:{...privacy, ...headers}});
}
function error(code, message, status = 400) {return json({error:{code,message}},status);}
function gzipAccepted(value) {
  for (const part of value.split(',')) {
    const [kind,...parameters] = part.trim().split(';').map(x=>x.trim());
    if (kind.toLowerCase() !== 'gzip') continue;
    const q = parameters.find(x=>x.startsWith('q='));
    return q == null || (q.slice(2).trim() !== '' && Number(q.slice(2)) > 0);
  }
  return false;
}

export function createQaRevisionApi({authorize, loadCurrent, loadRevision, loadCapabilities}) {
  return async request => {
    const {pathname,searchParams} = new URL(request.url);
    const binary = pathname.match(/^\/api\/v1\/graph\/([^/]+)\/(nodes\.bin|edges\.bin)$/);
    if (pathname !== PREFIX+'/graph' && pathname !== PREFIX+'/capabilities' && !binary) return null;
    if (!(await authorize(request))) return error('unauthorized','Owner session required',401);
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response(request.method === 'HEAD' ? null : 'Method Not Allowed',
        {status:405,headers:{...privacy,Allow:'GET, HEAD','Content-Type':'text/plain; charset=utf-8'}});
    }
    const finish = response => request.method === 'HEAD'
      ? new Response(null,{status:response.status,headers:response.headers}) : response;
    if (pathname === PREFIX+'/capabilities') return finish(json(await loadCapabilities()));
    if (!binary) {
      const mode = searchParams.get('mode') ?? 'current';
      const undated = searchParams.get('undated') ?? 'separate';
      if (!['current','known_then','known_now'].includes(mode) || !['separate','include','only'].includes(undated)) {
        return finish(error('invalid_selector','Unknown historical perspective or undated selection'));
      }
      const history = ['1','true'].includes(searchParams.get('history'));
      // Frozen adapter deliberately does not advertise history/as-of/session support.
      if (mode !== 'current' || undated !== 'separate' || ['session_id','run_id','output_snapshot_id'].some(key=>searchParams.has(key))) {
        return finish(error('selector_not_supported','This snapshot selector is not available',501));
      }
      const manifest = await loadCurrent({mode,at:null,history,undated});
      if (!manifest) return finish(json({status:'preparing',retry_after_ms:300,message:'Preparing complete graph and search index'},202,{'Retry-After':'1'}));
      return finish(json(manifest,200,{ETag:`"${manifest.revision}"`}));
    }
    let revision;
    try { revision = decodeURIComponent(binary[1]); } catch { revision = null; }
    const graph = revision && await loadRevision(revision);
    if (!graph) return finish(error('revision_expired','Snapshot revision is unavailable; reload the graph',409));
    const name = binary[2];
    const compressed = gzipAccepted(request.headers.get('accept-encoding') ?? '');
    const bytes = files.has(name) && await graph.artifact(name, compressed);
    if (!(bytes instanceof Uint8Array)) throw new Error('Missing prepared revision artifact');
    return new Response(request.method === 'HEAD' ? null : bytes,{headers:{
      ...privacy,ETag:`"${revision}-${name}"`,Vary:'Accept-Encoding',
      'Content-Encoding':compressed?'gzip':'identity',
      'Content-Type':'application/octet-stream','Content-Length':String(bytes.byteLength)
    }});
  };
}
