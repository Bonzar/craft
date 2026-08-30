import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const TOOL_TIMEOUT_SECONDS = 3600;

export function codexAgentMcpConfigured(value, serverPath) {
  const transport = value?.transport;
  const env = transport?.env;
  return value?.enabled === true
    && transport?.type === 'stdio'
    && transport.command === 'node'
    && Array.isArray(transport.args)
    && transport.args.length === 1
    && transport.args[0] === serverPath
    && env?.CRAFT_AGENT_BACKEND === 'codex'
    && env?.CRAFT_AGENT_PERMISSION === 'read-only'
    && value.tool_timeout_sec === TOOL_TIMEOUT_SECONDS;
}

export function setManagedAgentMcpTimeout(configPath, seconds = TOOL_TIMEOUT_SECONDS) {
  if (!Number.isInteger(seconds) || seconds <= 0) throw new Error('tool timeout must be a positive integer');
  const text = fs.readFileSync(configPath, 'utf8');
  const lines = text.split('\n');
  const header = '[mcp_servers.craft_agent]';
  const starts = lines.flatMap((line, index) => line.trim() === header ? [index] : []);
  if (starts.length !== 1) throw new Error('managed MCP section is missing or ambiguous');
  const start = starts[0];
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^\s*\[/.test(lines[index])) { end = index; break; }
  }
  const body = lines.slice(start + 1, end)
    .filter((line) => !/^\s*tool_timeout_sec\s*=/.test(line));
  const trailing = [];
  while (body.length && !body.at(-1).trim()) trailing.unshift(body.pop());
  const next = [
    ...lines.slice(0, start + 1),
    ...body,
    `tool_timeout_sec = ${seconds}`,
    ...trailing,
    ...lines.slice(end),
  ].join('\n');
  if (next === text) return false;
  const stat = fs.statSync(configPath);
  const temporary = path.join(path.dirname(configPath), `.${path.basename(configPath)}.craft-agent.${process.pid}`);
  try {
    fs.writeFileSync(temporary, next, { mode: stat.mode });
    fs.renameSync(temporary, configPath);
  } finally {
    try { fs.rmSync(temporary, { force: true }); } catch { /* already renamed */ }
  }
  return true;
}

async function cli() {
  const [mode, argument] = process.argv.slice(2);
  if (mode === 'check') {
    const raw = fs.readFileSync(0, 'utf8');
    let value;
    try { value = JSON.parse(raw); } catch { process.exitCode = 1; return; }
    if (!codexAgentMcpConfigured(value, argument)) process.exitCode = 1;
    return;
  }
  if (mode === 'set-timeout' && argument) {
    setManagedAgentMcpTimeout(argument);
    return;
  }
  throw new Error('usage: mcp-config.mjs check SERVER_PATH | set-timeout CONFIG_PATH');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  cli().catch((error) => {
    process.stderr.write(`${String(error?.message || error)}\n`);
    process.exitCode = 1;
  });
}
