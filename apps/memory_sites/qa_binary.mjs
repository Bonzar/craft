/**
 * Fixed wire formats for the unchanged QA Vite viewer. This is the transport
 * encoder, not the viewer's temporal projection, status or layout algorithm.
 */
export const qaFormats = Object.freeze({
  nodes: Object.freeze({format: "gv-nodes-le-v1", stride: 16}),
  edges: Object.freeze({format: "gv-edges-le-v1", stride: 8}),
});
const uint32 = n => Number.isInteger(n) && n >= 0 && n <= 0xffffffff;
const float32 = n => typeof n === "number" && Number.isFinite(Math.fround(n));

export function encodeQABuffers(nodes, edges) {
  if (!Array.isArray(nodes) || nodes.length > 100_000 ||
      !Array.isArray(edges) || edges.length > 1_000_000) {
    throw new RangeError("QA transport projection exceeds bounded build limits");
  }
  const nb = new ArrayBuffer(nodes.length * qaFormats.nodes.stride);
  const eb = new ArrayBuffer(edges.length * qaFormats.edges.stride);
  const nv = new DataView(nb), ev = new DataView(eb);
  for (let i = 0; i < nodes.length; i++) {
    const { ordinal, position, flags } = nodes[i];
    if (ordinal !== i || !Array.isArray(position) || position.length !== 3 ||
        ![position[0],position[1],position[2]].every(float32) || !uint32(flags)) throw new TypeError("Invalid node slot");
    const p = i * qaFormats.nodes.stride;
    position.forEach((x,j)=>nv.setFloat32(p+j*4,x,true));
    nv.setUint32(p+12,flags,true);
  }
  for (let i = 0; i < edges.length; i++) {
    const { ordinal, from, to } = edges[i];
    if (ordinal !== i || !uint32(from) || !uint32(to) ||
        from >= nodes.length || to >= nodes.length) throw new TypeError("Invalid edge slot");
    const p = i * qaFormats.edges.stride;
    ev.setUint32(p,from,true);
    ev.setUint32(p+4,to,true);
  }
  return {nodes: new Uint8Array(nb), edges: new Uint8Array(eb)};
}
