#!/usr/bin/env node
// Спросить инструмент признаков записи из терминала: что гейт увидит в этом
// вызове. Раньше это можно было выяснить только подсунув хуку событие — и
// расхождение двух гвардов по определению записи жило незамеченным.
//
// Использование:
//   tools/write-intent.mjs 'rm README.md'                 — команда
//   tools/write-intent.mjs --tool Edit --file README.md   — правка файла
//   tools/write-intent.mjs --tool mcp__x__craft_write     — прочая поверхность
//   echo '{"tool":"Bash","input":{"command":"ls"}}' | tools/write-intent.mjs --json
//
// Ответ печатается человеку; --json отдаёт разбор целиком.
import { writeIntent } from '../.claude/hooks/lib/write-intent.js';

const argv = process.argv.slice(2);
const flag = (name) => {
  const at = argv.indexOf(name);
  return at >= 0 ? argv[at + 1] : undefined;
};
const has = (name) => argv.includes(name);

function readStdin() {
  try {
    return require('node:fs').readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

let event;
if (has('--json') && !process.stdin.isTTY) {
  const raw = readStdin();
  try {
    event = JSON.parse(raw);
  } catch {
    process.stderr.write('на входе не JSON\n');
    process.exit(2);
  }
  event = { tool: event.tool || event.tool_name, input: event.input || event.tool_input || {} };
} else {
  const tool = flag('--tool') || 'Bash';
  const file = flag('--file');
  const positional = argv.filter((a, i) => !a.startsWith('--') && argv[i - 1] !== '--tool' && argv[i - 1] !== '--file');
  event = {
    tool,
    input: file ? { file_path: file } : { command: positional.join(' ') },
  };
}

const verdict = writeIntent(event);

if (has('--json')) {
  process.stdout.write(`${JSON.stringify(verdict, null, 2)}\n`);
  process.exit(0);
}

const state = verdict.writes ? 'ПИШЕТ' : (verdict.ephemeralOnly ? 'только временное' : 'не пишет');
process.stdout.write(`${state} — ${verdict.why}\n`);
if (verdict.targets.length) process.stdout.write(`цели: ${verdict.targets.join(', ')}\n`);
if (verdict.realTargets.length) process.stdout.write(`из них постоянные: ${verdict.realTargets.join(', ')}\n`);
