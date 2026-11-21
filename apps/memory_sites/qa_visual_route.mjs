import { frozenQaApi } from './qa_frozen_loader.mjs';
import {createQaSavedMaterialApi} from './qa_saved_material.mjs';

const policy = {
  'cache-control':'private, no-store',
  'x-content-type-options':'nosniff',
  'referrer-policy':'no-referrer',
  'content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
};
export const ownerPreview = r => Boolean(r.headers.get('oai-authenticated-user-id'));
const decode = b64 => Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
const error = (status,code,message) => Response.json({error:{code,message}},{status,headers:policy});

/** Host the exact read-only graph explorer assets and one synthetic QA revision.
 * Requires private owner-only Sites dispatch in addition to its identity header.
 * No real data, login passwords, origin bridge, external model or write routes.
 */
export function createQaVisualRoute({assets,snapshot, owner = ownerPreview}) {
  const compiled = new Map(Object.entries(assets).map(([path,v])=>[path,{type:v.type,bytes:decode(v.base64)}]));
  if (!compiled.has('/index.html')) throw Error('Synthetic QA UI is missing');
  const artifacts=Object.fromEntries(Object.entries(snapshot.artifacts).map(([p,v])=>[p,{
    identity:decode(v.identity), gzip:decode(v.gzip),
  }]));
  // Give the host raw graph bytes and let it negotiate wire compression.
  // Sending pre-gzipped bytes with a Content-Encoding header through
  // Workers' automatic encoder can yield a doubly encoded graph body.
  const api = frozenQaApi({...snapshot,artifacts},r=>owner(r)?'synthetic-readonly':null,{rawTransport:true});
  const overview = createQaSavedMaterialApi({manifest:snapshot.manifest,topics:snapshot.topics,overviews:Object.values(snapshot.overviews??{}),ownerSession:owner});
  return async request => {
    const {pathname:p} = new URL(request.url);
    const file = p==='/explore'||p==='/explore/' ? '/index.html' : p.startsWith('/assets/') ? p : null;
    if (!file && !p.startsWith('/api/v1/')) return null;
    if (!owner(request) || !file && request.headers.get('sec-fetch-site')==='cross-site') return error(401,'unauthorized','Sign in to the private test Site');
    if (!['GET','HEAD'].includes(request.method)) return error(405,'method_not_allowed','Read-only test');
    if (file) {
      const asset=compiled.get(file);
      return asset ? new Response(request.method==='HEAD'?null:asset.bytes,{headers:{...policy,'content-type':asset.type}})
        : error(404,'not_found','Asset not found');
    }
    return (await overview(request)) ?? (await api(request)) ?? error(501,'not_implemented','Not available on the synthetic snapshot');
  };
}
