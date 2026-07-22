#!/usr/bin/env node
/*
 * Mock Craft MCP server for evals.
 *
 * Exposes the same tool names as the real Craft MCP — mcp__Craft__craft_read /
 * mcp__Craft__craft_write — so a headless `claude -p` agent (which has NO real
 * Craft MCP: it is interactively authenticated and absent in headless runs) can
 * be evaluated exactly as in a live session.
 *
 *   craft_read  → served LIVE via the connect-link REST API (curl, which honours
 *                 the egress proxy) — real page state, real block IDs. Supports
 *                 the two forms the "Продукты" skill needs: `blocks get <id>` and
 *                 `search ... --include <q> --document <id>` (search is emulated
 *                 locally over the fetched subtree so it returns real IDs).
 *   craft_write → NOT executed. The raw command is appended to CRAFT_MOCK_WRITE_LOG
 *                 (one JSON line per call) and a plausible "ok" is returned. This
 *                 is the level-1 eval: we assert on the intended write, the real
 *                 base is never touched.
 *
 * Transport: MCP stdio, newline-delimited JSON-RPC 2.0.
 * Env: CRAFT_API_BASE (connect-link base w/ token), CRAFT_MOCK_WRITE_LOG (path).
 */
'use strict';
const fs = require('fs');
const { execFileSync } = require('child_process');

const BASE = (process.env.CRAFT_API_BASE || '').replace(/\/+$/, '');
const WRITE_LOG = process.env.CRAFT_MOCK_WRITE_LOG || '/tmp/craft-mock-write.log';

function curlBlocks(id, accept, maxDepth) {
  const url = `${BASE}/blocks?id=${encodeURIComponent(id)}&maxDepth=${maxDepth}`;
  return execFileSync('curl', ['-sS', '--fail', '--max-time', '60', '-H', `Accept: ${accept}`, url],
    { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
}

// Recursively collect blocks whose markdown matches a query (case-insensitive
// substring), returning "<text> [ID: <id>]" lines — mirrors real search output.
function walkSearch(node, q, rootId, out) {
  if (!node || typeof node !== 'object') return;
  const md = (node.markdown || '').trim();
  if (node.id && node.id.toLowerCase() !== rootId.toLowerCase() && md &&
      md.toLowerCase().includes(q.toLowerCase())) {
    out.push(`${md}  [ID: ${node.id}]`);
  }
  const kids = node.content || node.children || [];
  for (const k of kids) walkSearch(k, q, rootId, out);
}

function extractFlag(cmd, name) {
  // --name "value" | --name value
  let m = cmd.match(new RegExp(`--${name}\\s+"([^"]*)"`));
  if (m) return m[1];
  m = cmd.match(new RegExp(`--${name}\\s+'([^']*)'`));
  if (m) return m[1];
  m = cmd.match(new RegExp(`--${name}\\s+(\\S+)`));
  return m ? m[1] : null;
}

function handleRead(command) {
  const cmd = command.trim();
  if (/^search\b/.test(cmd)) {
    const doc = extractFlag(cmd, 'document');
    let q = extractFlag(cmd, 'include') || extractFlag(cmd, 'regexp');
    if (!q) { const m = cmd.match(/^search\s+"([^"]+)"|^search\s+'([^']+)'|^search\s+(\S+)/); q = m ? (m[1]||m[2]||m[3]) : ''; }
    if (!doc) return 'Mock error: search without --document is not supported in eval.';
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
    const json = /--format\s+json/.test(cmd);
    const depth = extractFlag(cmd, 'depth') || '-1';
    return curlBlocks(id, json ? 'application/json' : 'text/markdown', depth);
  }
  return `Mock error: unsupported craft_read command in eval: ${cmd.slice(0, 80)}`;
}

function handleWrite(command) {
  const rec = { ts: new Date().toISOString(), command };
  fs.appendFileSync(WRITE_LOG, JSON.stringify(rec) + '\n');
  return 'ok (mock: write intercepted for eval, not applied to Craft)';
}

// Descriptions mirror the real Craft MCP so the agent drives them the same way
// (passing "blocks get …" / "tasks update …" as the command arg) instead of
// hallucinating a `craft` CLI and reaching for Bash.
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
    return send({ jsonrpc: '2.0', id, result: {
      protocolVersion: '2024-11-05',
      capabilities: { tools: {} },
      serverInfo: { name: 'craft-mock', version: '0.1.0' } } });
  }
  if (method === 'notifications/initialized') return; // notification, no reply
  if (method === 'tools/list') {
    return send({ jsonrpc: '2.0', id, result: { tools: TOOLS } });
  }
  if (method === 'tools/call') {
    const name = params && params.name;
    const command = (params && params.arguments && params.arguments.command) || '';
    let text;
    try {
      text = name === 'craft_read' ? handleRead(command)
           : name === 'craft_write' ? handleWrite(command)
           : `Mock error: unknown tool ${name}`;
    } catch (e) {
      text = `Mock error: ${e.message}`;
    }
    return send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text }] } });
  }
  if (id !== undefined) send({ jsonrpc: '2.0', id, error: { code: -32601, message: `Method not found: ${method}` } });
}

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
