// Обрыв цепочки: КТО его ставит. Признак живёт в памяти процесса (lib/decided.js),
// и диспетчер по нему пропускает остаток цепочки, поэтому цена ошибки — молча
// отключённые хуки либо два ответа харнесу на один вызов.
//
// Проверяется различающая пара: исключающее решение признак ставит, дописанный
// контекст — нет. Проверять приходится в отдельном процессе: входы обёртки
// заканчиваются выходом из него, и под диспетчером выход перехвачен ровно так же,
// как здесь.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const LIB = path.join(REPO, '.claude', 'hooks', 'lib');
const url = (name) => JSON.stringify(pathToFileURL(path.join(LIB, name)).href);

const LOG_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'decided-test.'));
process.on('exit', () => fs.rmSync(LOG_DIR, { recursive: true, force: true }));

// Выход из процесса перехватывается тем же приёмом, что и в диспетчере: он и есть
// «этот хук закончил». Ответ пишется в служебный поток — в stdout лежит сам ответ
// хука, и мешать их нельзя.
function probe(calls) {
  const code = `
    import { deny, inject } from ${url('decide-claude.js')};
    import { wasDecided } from ${url('decided.js')};
    import { EVENTS } from ${url('event.js')};
    process.exit = () => { throw new Error('finished'); };
    const say = (what) => process.stderr.write(what + '\\n');
    ${calls}
  `;
  const res = spawnSync(process.execPath, ['--input-type=module', '-e', code], {
    encoding: 'utf8',
    input: JSON.stringify({ session_id: 'decided-sid', hook_event_name: 'PreToolUse', tool_name: 'Bash' }),
    env: {
      ...process.env,
      CRAFT_DECISION_LOG: path.join(LOG_DIR, `decisions.${Math.random()}.jsonl`),
      CRAFT_HOOK_NAME: 'universal-проба',
    },
  });
  return res.stderr.trim().split('\n');
}

test('запрет обрывает цепочку', () => {
  assert.deepEqual(probe(`
    try { deny('нельзя'); } catch { /* хук закончил себя */ }
    say('после запрета: ' + wasDecided());
  `), ['после запрета: true']);
});

test('дописанный контекст цепочку НЕ обрывает', () => {
  // Инжектор не исключает чужого ответа: их бывает несколько подряд, и пометка
  // от первого молча отключила бы всех, кто стоит следом.
  assert.deepEqual(probe(`
    try { inject(EVENTS.PRE_TOOL, 'текст в контекст'); } catch { /* хук закончил себя */ }
    say('после инжекта: ' + wasDecided());
  `), ['после инжекта: false']);
});
