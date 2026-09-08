// Перенос строк решения к наблюдателю — на живом диспетчере, а не на выдуманных
// записях: между «строки нет» и «строка не доехала» разница в ЗНАКЕ отказа, и
// проверять её надо на том же пути, каким она возникает.
//
// Два прогона диспетчера в отдельных процессах: первый решает, второй переносит.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const DISPATCH = path.join(REPO, '.claude', 'hooks', 'dispatch.js');

function session() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'drain-test.'));
  const log = path.join(dir, 'metrics.jsonl');
  const sid = `drain-${Math.random().toString(36).slice(2)}`;
  const send = (event) => spawnSync(process.execPath, [DISPATCH, 'universal'], {
    input: JSON.stringify({ session_id: sid, cwd: dir, ...event }),
    encoding: 'utf8',
    env: {
      ...process.env,
      CRAFT_STATE_DIR: dir,
      CRAFT_SESSION_ID: sid,
      CRAFT_METRICS_LOG: log,
      SESSION_ANCHOR_STATE: path.join(dir, 'anchor'),
      METRICS_STORE: 'off',
      HOOK_ONCE: 'off',
      SYNC_SYSTEM: 'off',
      PLAN_CLASSIFIER: 'off',
      // Кейс гоняет ЖИВОЙ диспетчер, в том числе на SessionStart, — значит
      // поднимает и инжекторы правил, и сборщик предодобренной зоны. Без этих
      // четырёх он ходил бы в connect-API и сносил `.claude/craft-gate-exempt-scope.txt`
      // САМОГО ЧЕКАУТА: файл гитигнорится, потеря невидима.
      CRAFT_API_BASE: '',
      CLAUDE_PROJECT_DIR: dir,
      CRAFT_GATE_EXEMPT_SCOPE: path.join(dir, 'exempt-scope.txt'),
      HOME: dir,
    },
  });
  const records = () => fs.readFileSync(log, 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  const summary = () => JSON.parse(fs.readFileSync(`${log}.summary.json`, 'utf8'));
  const journal = () => fs.readdirSync(dir).filter((n) => n.startsWith('decisions.'));
  return {
    dir, send, records, summary, journal,
  };
}

const DENIED = {
  hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_use_id: 'd1', tool_input: { command: 'sleep 30' },
};

test('решение прошлого события приезжает к наблюдателю следующим событием', () => {
  const s = session();
  try {
    const denied = s.send(DENIED);
    assert.match(denied.stdout, /"permissionDecision":\s*"deny"/, 'гвард отбил вызов');
    s.send({ hook_event_name: 'Stop' });
    const decision = s.records().find((r) => r.kind === 'decision');
    assert.equal(decision.outcome, 'deny');
    assert.equal(decision.hook, 'universal-sleep-waiter-guard');
    assert.deepEqual(s.summary().denies, { total: 1, by_class: { 'sleep-waiter-guard': 1 } });
    // В сводке САМОГО конца хода он же и неизвестен: блокировать его могут хуки,
    // которые ещё не отработали. Это «пока не знаем», а не «потеряли».
    assert.equal(s.summary().unknown_events, 1);
    // Конец сессии добирает строки последнего хода — и неизвестного не остаётся.
    s.send({ hook_event_name: 'SessionEnd', reason: 'clear' });
    assert.equal(s.summary().unknown_events, 0);
    assert.deepEqual(s.summary().denies, { total: 1, by_class: { 'sleep-waiter-guard': 1 } });
  } finally {
    fs.rmSync(s.dir, { recursive: true, force: true });
  }
});

test('журнал решений исчез между событиями — исход НЕИЗВЕСТЕН, а не «прошёл»', () => {
  const s = session();
  try {
    s.send(DENIED);
    // Ровно то, что делает уборщик с журналом кончившейся, как ему кажется,
    // сессии; то же самое видно при неписучем диске.
    for (const name of s.journal()) fs.rmSync(path.join(s.dir, name));
    s.send({ hook_event_name: 'Stop' });

    s.send({ hook_event_name: 'SessionEnd', reason: 'clear' });
    const summary = s.summary();
    assert.equal(summary.denies.total, 0, 'отказа мы не видели — и не выдумываем его');
    assert.equal(summary.unknown_events, 1, 'но и проходом его не считаем: видно числом');
  } finally {
    fs.rmSync(s.dir, { recursive: true, force: true });
  }
});

// Контракт записи проверяется на ТОМ, ЧТО ПИШЕТ ХУК, а не на форме, собранной
// руками в тесте: своя копия общей части в одном обработчике уже теряла признак
// диспетчера, и свёртка переставала спрашивать у реплики, доехали ли её строки.
test('под диспетчером КАЖДАЯ запись события несёт признак диспетчера', () => {
  const s = session();
  try {
    s.send({ hook_event_name: 'SessionStart', source: 'startup' });
    s.send({ hook_event_name: 'UserPromptSubmit', prompt: 'поехали работать над задачей' });
    s.send({ hook_event_name: 'PreToolUse', tool_name: 'Read', tool_use_id: 'r1', tool_input: { file_path: '/tmp/x' } });
    s.send({
      hook_event_name: 'PostToolUse', tool_name: 'Read', tool_use_id: 'r1', tool_input: { file_path: '/tmp/x' }, tool_response: {},
    });
    s.send({ hook_event_name: 'Stop' });

    // Записи СОБЫТИЙ, а не всё подряд: строки канала (замеры, решения) тоже
    // несут ключ, но признака диспетчера у них нет и быть не должно.
    const EVENT_KINDS = ['session', 'prompt', 'pre', 'post', 'fail', 'stop'];
    const own = s.records().filter((r) => EVENT_KINDS.includes(r.kind));
    assert.ok(own.length >= 5, `записей события ожидалось не меньше пяти, вышло ${own.length}`);
    const forgot = own.filter((r) => r.disp !== true).map((r) => r.kind);
    assert.deepEqual(forgot, [], 'признак диспетчера обязан быть у всех записей');
  } finally {
    fs.rmSync(s.dir, { recursive: true, force: true });
  }
});

test('потерянный признак инцидента виден числом на ЖИВОМ пути', () => {
  const s = session();
  try {
    s.send({ hook_event_name: 'UserPromptSubmit', prompt: 'ты сломал мою заметку, откатись немедленно' });
    for (const name of s.journal()) fs.rmSync(path.join(s.dir, name));
    s.send({ hook_event_name: 'Stop' });
    s.send({ hook_event_name: 'SessionEnd', reason: 'clear' });

    const summary = s.summary();
    assert.equal(summary.incidents.detected, 0, 'признака мы не видели');
    assert.ok(summary.unknown_events >= 1, 'но реплика названа неизвестной, а не «инцидента не было»');
  } finally {
    fs.rmSync(s.dir, { recursive: true, force: true });
  }
});
