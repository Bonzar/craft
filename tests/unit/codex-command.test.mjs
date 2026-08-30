import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveCodexCommand } from '../../adapters/codex/lib/command.mjs';

test('an explicit Codex command override always wins', () => {
  assert.equal(resolveCodexCommand(
    { CRAFT_CODEX_CMD: '/opt/codex-next' },
    () => true,
    'darwin',
  ), '/opt/codex-next');
});

test('the desktop bundle wins over a stale PATH binary on macOS', () => {
  const bundle = '/Applications/ChatGPT.app/Contents/Resources/codex';
  assert.equal(resolveCodexCommand({}, (candidate) => candidate === bundle, 'darwin'), bundle);
});

test('other environments retain the portable PATH fallback', () => {
  assert.equal(resolveCodexCommand({}, () => false, 'linux'), 'codex');
});
