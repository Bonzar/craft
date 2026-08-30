import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { buildContext } from '../../.codex/hooks/craft-session-start.mjs';

test('Codex SessionStart returns cached router and code rules as developer context', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-codex-hook-'));
  try {
    const router = path.join(dir, 'router.md');
    const code = path.join(dir, 'code.md');
    fs.writeFileSync(router, 'ROUTER-CONTEXT');
    fs.writeFileSync(code, 'CODE-RULES-CONTEXT');

    const context = buildContext(
      { source: 'compact' },
      {
        ...process.env,
        CRAFT_CODEX_SKIP_REFRESH: '1',
        CRAFT_CODEX_LIVE_DIR: dir,
        CRAFT_CODEX_ROUTER_SNAPSHOT: router,
        CRAFT_CODEX_CODE_SNAPSHOT: code,
      },
    );

    assert.match(context, /ROUTER-CONTEXT/);
    assert.match(context, /CODE-RULES-CONTEXT/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Codex SessionStart has an actionable fallback without snapshots', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-codex-hook-empty-'));
  try {
    const context = buildContext(
      { source: 'compact' },
      {
        ...process.env,
        CRAFT_CODEX_SKIP_REFRESH: '1',
        CRAFT_CODEX_LIVE_DIR: dir,
        CRAFT_CODEX_ROUTER_SNAPSHOT: path.join(dir, 'missing-router.md'),
        CRAFT_CODEX_CODE_SNAPSHOT: path.join(dir, 'missing-code.md'),
      },
    );
    assert.match(context, /Craft router was not loaded/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
