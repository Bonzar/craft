// Форма ответа хука: компактный JSON, порядок ключей как у jq. Эталон берётся
// не из головы — тот же объект прогоняется через jq и сравнивается с выводом
// модуля.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
// Форму ответа печатает ОБЁРТКА харнеса; общая часть знает только словарь
// исходов, печатать ей нечем.
const DECIDE = path.join(REPO, '.claude', 'hooks', 'lib', 'decide-claude.js');

// Журнал решений уводится во временный файл: решение пишет строку туда, и без
// подмены прогон сорил бы в каталог состояния живой сессии.
import fs from 'node:fs';
import os from 'node:os';
const LOG_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'decide-test.'));
const ENV = { ...process.env, CRAFT_DECISION_LOG: path.join(LOG_DIR, 'decisions.jsonl') };
process.on('exit', () => fs.rmSync(LOG_DIR, { recursive: true, force: true }));

const hasJq = spawnSync('jq', ['--version'], { stdio: 'ignore' }).status === 0;

// Как печатал bash: jq -cn --arg r "…" '{hookSpecificOutput:{…}}'.
function viaJq(filter, reason) {
  const res = spawnSync('jq', ['-cn', '--arg', 'r', reason, filter], { encoding: 'utf8' });
  return res.stdout;
}

// Как печатает модуль: вызов в отдельном процессе, потому что решение
// заканчивается выходом.
function viaModule(fn, reason) {
  const code = `import { ${fn} } from ${JSON.stringify(DECIDE)}; ${fn}(process.argv[1]);`;
  const res = spawnSync(process.execPath, ['--input-type=module', '-e', code, reason], { encoding: 'utf8', env: ENV, input: '' });
  return res.stdout;
}

const REASONS = [
  'Заблокировано план-гейтом: цель не входит в одобренный план.',
  'Кавычки "двойные", ёлочки «внутри» и обратный слэш \\ — всё это уходит в текст причины',
  'Перенос строки в причине:\nвторая строка',
];

test('запрет печатается той же формой, что печатал jq', { skip: !hasJq && 'нет jq' }, () => {
  const filter = '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}';
  for (const reason of REASONS) {
    assert.equal(viaModule('deny', reason), viaJq(filter, reason));
  }
});

test('вопрос человеку печатается той же формой', { skip: !hasJq && 'нет jq' }, () => {
  const filter = '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}';
  assert.equal(viaModule('ask', REASONS[0]), viaJq(filter, REASONS[0]));
});

test('блокировка конца хода печатается той же формой', { skip: !hasJq && 'нет jq' }, () => {
  const filter = '{"decision":"block","reason":$r}';
  for (const reason of REASONS) {
    assert.equal(viaModule('block', reason), viaJq(filter, reason));
  }
});

// Проход — это молчание самого гварда, а не вход в обёртке: печатать на проходе
// нечего, и строки в журнале у него нет. Вход, который только выходил из
// процесса, стоял без вызывающих и давал третий способ закончить хук.
test('у обёртки ровно четыре входа — и прохода среди них нет', async () => {
  const mod = await import(pathToFileURL(DECIDE).href);
  assert.deepEqual(Object.keys(mod).sort(), ['ask', 'block', 'deny', 'inject']);
});
