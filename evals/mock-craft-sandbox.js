#!/usr/bin/env node
/*
 * Mock Craft MCP server for the LEVEL-2 (end-to-end) Продукты eval.
 *
 * Difference from the L1 mock (mock-craft-mcp.js): L1 INTERCEPTS writes (logs the
 * intended command, applies nothing). This L2 mock APPLIES writes for real — but
 * every write is redirected to a disposable SANDBOX subtree and hard-guarded so it
 * can NEVER touch the real «Продукты» page. The agent runs the exact same skill;
 * only the target is swapped underneath it.
 *
 *   craft_read  → any read whose id normalizes to the real Продукты page
 *                 (395450FC-…) is REDIRECTED to the sandbox root (CRAFT_SANDBOX_ROOT)
 *                 so the agent only ever sees SANDBOX products and SANDBOX block ids.
 *                 Redirect is fail-closed: if CRAFT_SANDBOX_ROOT is unset the read
 *                 THROWS rather than leak the real page (which would hand the agent
 *                 real product ids). Other reads proxy to connect-REST like L1.
 *   craft_write → PARSED and APPLIED to the sandbox via connect-REST, after two
 *                 guards fire in order (see assertWriteAllowed):
 *                   1. page-id guard  — no id in the write may normalize to the real
 *                                       page id; on match THROW, issue NO HTTP.
 *                   2. membership guard — the write target must live inside the
 *                                       CRAFT_SANDBOX_ROOT subtree; else THROW, no
 *                                       write HTTP.
 *                 Every applied write is appended to CRAFT_MOCK_WRITE_LOG (one JSON
 *                 line: {ts, command, method, url, body}) exactly like L1 logs the
 *                 command, so the runner can assert on it.
 *
 * Transport: MCP stdio, newline-delimited JSON-RPC 2.0 (identical scaffolding + tool
 * descriptions to the L1 mock, so the agent drives the tools the same way).
 *
 * Env:
 *   CRAFT_API_BASE       connect-link REST base w/ token (reads + sandbox writes).
 *   CRAFT_SANDBOX_ROOT   root block id of the live sandbox subtree (set by run-e2e.sh).
 *   CRAFT_MOCK_WRITE_LOG path for the applied-write log.
 *   CRAFT_SELFCHECK=1    self-check mode: NO network is ever issued (every httpCurl
 *                        throws before curl) and REST calls are counted, so the guard
 *                        pre-flight in run-e2e.sh can prove the guard fires with zero
 *                        REST calls without any risk of touching the live base.
 *
 * This file is BOTH an MCP server (when run directly) and a require()-able module
 * (for the guard self-check): the stdio loop only starts under `require.main`.
 */
'use strict';
const fs = require('fs');
const { execFileSync } = require('child_process');

const BASE = (process.env.CRAFT_API_BASE || '').replace(/\/+$/, '');
const WRITE_LOG = process.env.CRAFT_MOCK_WRITE_LOG || '/tmp/craft-e2e-write.log';
const SANDBOX_ROOT = process.env.CRAFT_SANDBOX_ROOT || '';
const SELFCHECK = process.env.CRAFT_SELFCHECK === '1';

// The one page that must be impossible to write. Normalized = lowercased, hyphenless.
const REAL_PAGE = '395450FC-468E-4EF6-8267-BC158A4E2EBC';
function norm(id) { return String(id || '').toLowerCase().replace(/-/g, ''); }
const REAL_PAGE_NORM = norm(REAL_PAGE); // 395450fc468e4ef68267bc158a4e2ebc

// ---- network: the SINGLE path to the base. Counted so the self-check can prove
// "zero REST calls"; in SELFCHECK it refuses to touch the network at all, so even a
// broken guard cannot reach the live base during the pre-flight. ----
let REST_CALLS = 0;
function httpCurl(method, pathAndQuery, accept, body) {
  REST_CALLS++;
  if (SELFCHECK) throw new Error(`SELFCHECK: network blocked (${method} ${pathAndQuery})`);
  const url = `${BASE}${pathAndQuery}`;
  const args = ['-sS', '--fail', '--max-time', '60', '-X', method, '-H', `Accept: ${accept}`];
  if (body != null) args.push('-H', 'Content-Type: application/json', '--data', JSON.stringify(body));
  args.push(url);
  return execFileSync('curl', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
}
function curlBlocks(id, accept, maxDepth) {
  return httpCurl('GET', `/blocks?id=${encodeURIComponent(id)}&maxDepth=${maxDepth}`, accept, null);
}

// ---- isolation guards ----
function assertNoRealPage(ids) {
  for (const id of ids) {
    if (id && norm(id) === REAL_PAGE_NORM) {
      throw new Error(`ISOLATION GUARD: write addresses the REAL «Продукты» page (${id}) — refused, NO HTTP issued`);
    }
  }
}
function collectIds(node, set) {
  if (!node || typeof node !== 'object') return;
  if (node.id) set.add(norm(node.id));
  for (const k of (node.content || node.children || [])) collectIds(k, set);
}
function sandboxIdSet() {
  const json = JSON.parse(curlBlocks(SANDBOX_ROOT, 'application/json', -1));
  const root = json.data ? json.data[0] : json;
  const set = new Set();
  collectIds(root, set);
  return set;
}
// Every write target must live inside the sandbox subtree. This is the airtight
// backstop behind the page-id guard: even if the agent somehow held a real product
// id (it cannot — reads are redirected), that id is not in the sandbox and is refused.
function assertInSandbox(id) {
  if (!SANDBOX_ROOT) throw new Error('ISOLATION GUARD: CRAFT_SANDBOX_ROOT unset — refusing every write');
  if (norm(id) === norm(SANDBOX_ROOT)) return; // the root itself is in-scope
  const set = sandboxIdSet();
  if (!set.has(norm(id))) {
    throw new Error(`ISOLATION GUARD: write target ${id} is not inside the sandbox subtree — refused, NO write HTTP`);
  }
}
// Guards for a single resolved write. Order matters: the page-id check is pure
// (no I/O) and runs first, so a real-page write throws before any network.
function assertWriteAllowed(targetIds) {
  assertNoRealPage(targetIds);
  for (const id of targetIds) assertInSandbox(id);
}

// ---- write command parsing + application ----
function extractFlag(cmd, name) {
  let m = cmd.match(new RegExp(`--${name}\\s+"([^"]*)"`));
  if (m) return m[1];
  m = cmd.match(new RegExp(`--${name}\\s+'([^']*)'`));
  if (m) return m[1];
  m = cmd.match(new RegExp(`--${name}\\s+(\\S+)`));
  return m ? m[1] : null;
}
function extractJson(sub) {
  // --json is authored last; grab everything after it, stripping one quote layer.
  const i = sub.indexOf('--json');
  if (i < 0) return null;
  let rest = sub.slice(i + '--json'.length).trim();
  if ((rest[0] === '"' && rest.endsWith('"')) || (rest[0] === "'" && rest.endsWith("'"))) rest = rest.slice(1, -1);
  return rest;
}
function logApplied(command, method, body) {
  fs.appendFileSync(WRITE_LOG, JSON.stringify({ ts: new Date().toISOString(), command, method, url: `${BASE}/blocks`, body }) + '\n');
}

// Apply ONE sub-command (already split off a ;-batch). Returns a short summary.
function applyOne(sub) {
  sub = sub.trim();
  if (!sub) return '';

  // tasks update --task|--id <id> --state todo|done|canceled  → PUT taskInfo.state
  if (/^tasks\s+update\b/.test(sub)) {
    const id = extractFlag(sub, 'task') || extractFlag(sub, 'id');
    const state = extractFlag(sub, 'state');
    if (!id) throw new Error('tasks update: no --task/--id id parsed');
    if (!state) throw new Error('tasks update: no --state parsed');
    assertWriteAllowed([id]);
    const body = { blocks: [{ id, taskInfo: { state } }] };
    httpCurl('PUT', '/blocks', 'application/json', body);
    logApplied(sub, 'PUT', body);
    return `tasks update ${id} → state=${state}`;
  }

  // blocks update --id <id> --json <block>  → PUT (merge id in)
  if (/^blocks\s+update\b/.test(sub)) {
    const id = extractFlag(sub, 'id');
    const raw = extractJson(sub);
    if (!id) throw new Error('blocks update: no --id parsed');
    if (!raw) throw new Error('blocks update: no --json parsed');
    const obj = JSON.parse(raw);
    assertWriteAllowed([id, obj.id].filter(Boolean));
    const body = { blocks: [Object.assign({}, obj, { id })] };
    httpCurl('PUT', '/blocks', 'application/json', body);
    logApplied(sub, 'PUT', body);
    return `blocks update ${id}`;
  }

  // blocks add --id <pageId> --json <block> [--position start|end]  → POST into page
  if (/^blocks\s+add\b/.test(sub)) {
    const pageId = extractFlag(sub, 'id');
    const sibling = extractFlag(sub, 'siblingId');
    const raw = extractJson(sub);
    if (!raw) throw new Error('blocks add: no --json parsed');
    const block = JSON.parse(raw);
    if (sibling && !pageId) {
      // Sibling-relative insert is not exercised by the Продукты skill (it adds a new
      // product straight into a category page). Refuse rather than issue an unverified
      // REST shape — the guard would pass but we won't guess the position payload.
      throw new Error('blocks add --siblingId is not supported by the e2e mock (use --id <categoryPageId>)');
    }
    if (!pageId) throw new Error('blocks add: no --id <pageId> parsed');
    assertWriteAllowed([pageId]);
    const position = { position: extractFlag(sub, 'position') || 'end', pageId };
    const body = { blocks: [block], position };
    httpCurl('POST', '/blocks', 'application/json', body);
    logApplied(sub, 'POST', body);
    return `blocks add → page ${pageId}`;
  }

  // blocks delete --id|--ids <a,b,…>  → DELETE
  if (/^blocks\s+delete\b/.test(sub)) {
    const ids = (extractFlag(sub, 'ids') || extractFlag(sub, 'id') || '').split(',').map(s => s.trim()).filter(Boolean);
    if (!ids.length) throw new Error('blocks delete: no ids parsed');
    assertWriteAllowed(ids);
    const body = { blockIds: ids };
    httpCurl('DELETE', '/blocks', 'application/json', body);
    logApplied(sub, 'DELETE', body);
    return `blocks delete ${ids.join(',')}`;
  }

  throw new Error(`e2e mock: unsupported craft_write command: ${sub.slice(0, 80)}`);
}

// Public write entry: split a ;-batch, apply each guarded. If any sub throws (guard
// or parse), the whole call throws — the tools/call handler surfaces it to the agent.
function applyWriteCommand(command) {
  const parts = String(command).split(';').map(s => s.trim()).filter(Boolean);
  const out = [];
  for (const p of parts) out.push(applyOne(p));
  return `ok (e2e: applied to sandbox) — ${out.join(' | ')}`;
}

// ---- read handling (mirrors L1; only the id is redirected) ----
function redirectId(id) {
  if (id && norm(id) === REAL_PAGE_NORM) {
    if (!SANDBOX_ROOT) throw new Error('e2e mock: read of the real «Продукты» page requested but CRAFT_SANDBOX_ROOT is unset — refusing to serve the real page');
    return SANDBOX_ROOT;
  }
  return id;
}
function walkSearch(node, q, rootId, out) {
  if (!node || typeof node !== 'object') return;
  const md = (node.markdown || '').trim();
  if (node.id && node.id.toLowerCase() !== rootId.toLowerCase() && md &&
      md.toLowerCase().includes(q.toLowerCase())) {
    out.push(`${md}  [ID: ${node.id}]`);
  }
  for (const k of (node.content || node.children || [])) walkSearch(k, q, rootId, out);
}
function handleRead(command) {
  const cmd = command.trim();
  if (/^search\b/.test(cmd)) {
    let doc = extractFlag(cmd, 'document');
    let q = extractFlag(cmd, 'include') || extractFlag(cmd, 'regexp');
    if (!q) { const m = cmd.match(/^search\s+"([^"]+)"|^search\s+'([^']+)'|^search\s+(\S+)/); q = m ? (m[1] || m[2] || m[3]) : ''; }
    if (!doc) return 'Mock error: search without --document is not supported in eval.';
    doc = redirectId(doc);
    const json = JSON.parse(curlBlocks(doc, 'application/json', -1));
    const root = json.data ? json.data[0] : json;
    const out = [];
    walkSearch(root, q, doc, out);
    return out.length ? out.join('\n') : `No matches for "${q}" in ${doc}.`;
  }
  if (/^blocks\s+get\b/.test(cmd)) {
    let id = extractFlag(cmd, 'id');
    if (!id) { const m = cmd.match(/^blocks\s+get\s+([A-Za-z0-9-]{8,})/); id = m ? m[1] : null; }
    if (!id) return 'Mock error: could not parse block id from command.';
    id = redirectId(id);
    const json = /--format\s+json/.test(cmd);
    const depth = extractFlag(cmd, 'depth') || '-1';
    return curlBlocks(id, json ? 'application/json' : 'text/markdown', depth);
  }
  return `Mock error: unsupported craft_read command in eval: ${cmd.slice(0, 80)}`;
}

// ---- MCP stdio server (tool set + descriptions identical to the L1 mock) ----
const TOOLS = [
  { name: 'craft_read',
    description: 'Read/search Craft; batch with semicolons. Pass a CLI-style command as the "command" arg — do NOT use Bash. Cmds:\n  documents resolve-link <url> -> rootBlockId\n  blocks get <rootBlockId|blockId> [--depth <n>] [--format json|markdown]\n  blocks get --date today\n  tasks list [--scope active|upcoming|inbox|logbook|document|all]\n  search <query> [--include <text>] [--document <rootBlockId>] [--location ...]\n  collections list/schema/items-get\nFor Craft links run "documents resolve-link <url>" first; use the returned rootBlockId for blocks get.',
    inputSchema: { type: 'object', properties: { command: { type: 'string', description: 'CLI-style Craft read command, e.g. "blocks get <id> --depth -1" or "search <q> --document <id>"' } }, required: ['command'] } },
  { name: 'craft_write',
    description: 'Write to Craft; batch with semicolons. Pass a CLI-style command as the "command" arg — do NOT use Bash. Cmds:\n  tasks add --markdown <text> [--state todo|done|canceled] [--schedule <date>]\n  tasks update --task <taskId> --state todo|done|canceled  (several via ";", one --task each)\n  blocks add --id <pageId> --json <blockJson> [--position start|end]\n  blocks add --siblingId <blockId> --json <blockJson> [--position before|after]\n  blocks update --id <blockId> --json <blockJson>\n  blocks move --id <blockId> --targetId <pageId>\n  blocks delete --id/--ids\nWrites go through --json; the bare --markdown flag is not used for block writes.',
    inputSchema: { type: 'object', properties: { command: { type: 'string', description: 'CLI-style Craft write command, e.g. "tasks update --task <id> --state done"' } }, required: ['command'] } },
];

function send(msg) { process.stdout.write(JSON.stringify(msg) + '\n'); }
function handle(req) {
  const { id, method, params } = req;
  if (method === 'initialize') {
    return send({ jsonrpc: '2.0', id, result: { protocolVersion: '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'craft-mock-sandbox', version: '0.1.0' } } });
  }
  if (method === 'notifications/initialized') return;
  if (method === 'tools/list') return send({ jsonrpc: '2.0', id, result: { tools: TOOLS } });
  if (method === 'tools/call') {
    const name = params && params.name;
    const command = (params && params.arguments && params.arguments.command) || '';
    let text;
    try {
      text = name === 'craft_read' ? handleRead(command)
           : name === 'craft_write' ? applyWriteCommand(command)
           : `Mock error: unknown tool ${name}`;
    } catch (e) {
      text = `Mock error: ${e.message}`;
    }
    return send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text }] } });
  }
  if (id !== undefined) send({ jsonrpc: '2.0', id, error: { code: -32601, message: `Method not found: ${method}` } });
}

if (require.main === module) {
  let buf = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    buf += chunk;
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let req; try { req = JSON.parse(line); } catch { continue; }
      handle(req);
    }
  });
  process.stdin.on('end', () => process.exit(0));
}

// Exposed for the run-e2e.sh guard pre-flight self-check.
module.exports = {
  REAL_PAGE, REAL_PAGE_NORM, norm,
  assertNoRealPage, assertInSandbox, assertWriteAllowed,
  applyWriteCommand, applyOne,
  getRestCalls: () => REST_CALLS,
  resetRestCalls: () => { REST_CALLS = 0; },
};
