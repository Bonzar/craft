#!/usr/bin/env node
import readline from 'node:readline';
import { handleAgentRpc } from './lib/mcp.mjs';

const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  if (!line.trim()) continue;
  let response;
  try {
    response = await handleAgentRpc(JSON.parse(line));
  } catch (error) {
    response = { jsonrpc: '2.0', id: null, error: { code: -32700, message: String(error?.message || error) } };
  }
  if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
}
