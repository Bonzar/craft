import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { runPlanGateRules } from '../../core/plan-gate/rules/index.js';
import { anchorRule } from '../../core/plan-gate/rules/anchor.js';

test('an exempt database target does not consume or log the global switch', () => {
  const scopeFile = path.join(os.tmpdir(), `plan-gate-scope-${process.pid}-${Date.now()}`);
  fs.writeFileSync(scopeFile, 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE\n');
  let switchLogs = 0;
  try {
    const result = runPlanGateRules({
      intent: {
        category: 'database',
        ids: ['AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'],
      },
      exemptions: { anchor: true },
      anchorFile: '',
      goals: () => [{ source: 'switch' }],
      switchAt: () => 0,
      appendSwitchLog: () => { switchLogs += 1; },
      scopeFile,
      registryDecision: () => ({ decision: 'deny', rule: 'registry' }),
    });
    assert.equal(result.decision, 'allow');
    assert.equal(result.rule, 'exempt-scope');
    assert.equal(switchLogs, 0);
  } finally {
    fs.rmSync(scopeFile, { force: true });
  }
});

test('a throwing rule becomes a deny decision', () => {
  const result = runPlanGateRules({}, [() => { throw new Error('backend unavailable'); }]);
  assert.equal(result.decision, 'deny');
  assert.equal(result.rule, 'failure');
});

test('missing anchor state address is fail-closed unless explicitly exempt', () => {
  assert.equal(anchorRule({ exemptions: {}, anchorFile: '' }).decision, 'deny');
  assert.equal(anchorRule({ exemptions: { anchor: true }, anchorFile: '' }).decision, 'abstain');
});
