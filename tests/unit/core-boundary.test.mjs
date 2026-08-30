import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { capability } from '../../core/runtime/capabilities.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? files(file) : [file];
  });
}

test('core contains no provider, harness, or concrete model names', () => {
  const forbidden = /claude|codex|haiku|sonnet|opus|gpt-[0-9]|\bterra\b|\bsol\b|\bspark\b/i;
  const leaks = [];
  for (const file of files(path.join(repo, 'core'))) {
    const match = forbidden.exec(fs.readFileSync(file, 'utf8'));
    if (match) leaks.push(`${path.relative(repo, file)}: ${match[0]}`);
  }
  assert.deepEqual(leaks, []);
});

test('core policy contains no native tool vocabulary or harness wire fields', () => {
  const forbidden = /\b(Bash|Write|Edit|MultiEdit|NotebookEdit|apply_patch|Workflow|ExitPlanMode|AskUserQuestion|spawn_agent|create_thread|send_message_to_thread|request_user_input)\b|tool_name|tool_input|subagent_type|hook_event_name/;
  const leaks = [];
  for (const file of files(path.join(repo, 'core'))) {
    if (file.includes(`${path.sep}vendor${path.sep}`)) continue;
    const match = forbidden.exec(fs.readFileSync(file, 'utf8'));
    if (match) leaks.push(`${path.relative(repo, file)}: ${match[0]}`);
  }
  assert.deepEqual(leaks, []);
});

test('core capability set is a union and adapters may implement it asymmetrically', () => {
  const contract = JSON.parse(fs.readFileSync(path.join(repo, 'core/capabilities.json'), 'utf8'));
  const first = JSON.parse(fs.readFileSync(path.join(repo, 'adapters/claude/config.json'), 'utf8'));
  const second = JSON.parse(fs.readFileSync(path.join(repo, 'adapters/codex/config.json'), 'utf8'));
  const feature = 'native-subagent-lifecycle';
  assert.ok(contract.features.includes(feature));
  assert.equal(capability(first, feature).status, 'unsupported');
  assert.equal(capability(second, feature).status, 'supported');
});
