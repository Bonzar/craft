import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const DECIDE = path.join(REPO, '.claude', 'hooks', 'lib', 'decide.js');

// Как печатает модуль: вызов в отдельном процессе, потому что решение
// заканчивается выходом.
function viaModule(fn, reason) {
  const code = `import { ${fn} } from ${JSON.stringify(DECIDE)}; ${fn}(process.argv[1]);`;
  const res = spawnSync(process.execPath, ['--input-type=module', '-e', code, reason], { encoding: 'utf8' });
  return res.stdout;
}

const REASONS = [
  'Заблокировано план-гейтом: цель не входит в одобренный план.',
  'Кавычки "двойные", ёлочки «внутри» и обратный слэш \\ — всё это уходит в текст причины',
  'Перенос строки в причине:\nвторая строка',
];

test('core emits a provider-neutral deny decision', () => {
  for (const reason of REASONS) {
    assert.deepEqual(JSON.parse(viaModule('deny', reason)), { type: 'deny', reason });
  }
});

test('core emits a provider-neutral ask decision', () => {
  assert.deepEqual(JSON.parse(viaModule('ask', REASONS[0])), { type: 'ask', reason: REASONS[0] });
});

test('core emits a provider-neutral block decision', () => {
  for (const reason of REASONS) {
    assert.deepEqual(JSON.parse(viaModule('block', reason)), { type: 'block', reason });
  }
});

test('проход молчит', () => {
  const code = `import { allow } from ${JSON.stringify(DECIDE)}; allow();`;
  const res = spawnSync(process.execPath, ['--input-type=module', '-e', code], { encoding: 'utf8' });
  assert.equal(res.stdout, '');
  assert.equal(res.status, 0);
});
