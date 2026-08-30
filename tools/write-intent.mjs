#!/usr/bin/env node
import { writeIntent } from '../core/plan-gate/write-intent.js';

// Canonical diagnostic CLI. Native tool names belong to adapters; this helper
// accepts the same route/payload contract as core.
const route = process.argv[2] || 'command.run';
let payload;
if (route === 'command.run') {
  payload = { command: process.argv.slice(3).join(' '), unbounded: false };
} else {
  try {
    payload = JSON.parse(process.argv[3] || '{}');
  } catch {
    process.stderr.write('payload must be valid JSON\n');
    process.exit(2);
  }
}
const action = {
  route,
  payload,
};
process.stdout.write(`${JSON.stringify(writeIntent(action), null, 2)}\n`);
