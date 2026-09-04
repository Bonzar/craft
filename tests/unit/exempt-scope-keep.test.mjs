// Сборщик предодобренной зоны не сносит прежний снимок, пока не убедился, что
// собирать вообще из чего.
//
// Снимок гитигнорится: его пропажа не видна ни в `git status`, ни глазами, а гейт
// после неё молча перестаёт пропускать предодобренные записи — до следующего
// старта сессии. Значит «снёс и ушёл, не найдя списка» — это тихая потеря, а не
// осторожность. Ловится только живым запуском: порядок двух операций в файле
// глазами читается одинаково при любом ответе.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HOOK = path.join(REPO, '.claude', 'hooks', 'universal-cache-gate-exempt-scope.js');

function run({ pages }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'exempt-scope-test.'));
  const snapshot = path.join(dir, 'scope.txt');
  fs.writeFileSync(snapshot, 'ПРЕЖНИЙ СНИМОК\n');
  spawnSync(process.execPath, [HOOK], {
    input: JSON.stringify({ hook_event_name: 'SessionStart', session_id: 'scope-test', source: 'startup' }),
    encoding: 'utf8',
    env: {
      ...process.env,
      CRAFT_GATE_EXEMPT_SCOPE: snapshot,
      CRAFT_GATE_EXEMPT_PAGES: pages === 'нет' ? path.join(dir, 'нет-такого-списка.txt') : pages,
      CRAFT_API_BASE: '',
      HOOK_ONCE: 'off',
    },
  });
  const kept = fs.existsSync(snapshot);
  fs.rmSync(dir, { recursive: true, force: true });
  return kept;
}

test('без списка страниц прежний снимок зоны НЕ сносится', () => {
  assert.equal(run({ pages: 'нет' }), true, 'списка нет — сносить прежнюю зону не за что');
});

test('со списком, но без доступа к сети, прежний снимок сносится', () => {
  // Обратная сторона того же правила: сборка НАЧАЛАСЬ и не удалась — устаревшая
  // зона не должна выдавать себя за свежую, и гейт возвращается к «гейтить всё».
  const pages = path.join(REPO, 'tests', 'hooks', 'fixtures', 'gate-exempt-pages.txt');
  assert.ok(fs.existsSync(pages), 'список-образец обязан лежать рядом: без него кейс молча ничего не проверял бы');
  assert.equal(run({ pages }), false);
});
