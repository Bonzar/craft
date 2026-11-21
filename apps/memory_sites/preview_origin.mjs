// Keep browser POSTs same-origin. An opaque referrer policy may remove
// Origin from HTML form submissions; trust only browser Fetch Metadata
// that explicitly confirms same-origin in that case.
export function sameOriginPreviewPost(request) {
  if (request.method!=='POST') return false;
  const origin=request.headers.get('origin');
  const relation=request.headers.get('sec-fetch-site');
  if (relation==='cross-site'||relation==='same-site') return false;
  if (origin===new URL(request.url).origin) return true;
  return (origin===null||origin==='null')&&relation==='same-origin';
}
