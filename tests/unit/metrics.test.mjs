// Ядро метрик: класс причины, счёт токенов хода, нормализация remote.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(HERE, '..', 'hooks', 'fixtures');
const metrics = await import('../../.claude/hooks/lib/metrics.js');

test('класс причины: план-гейт по тексту, остальные по имени хука', () => {
  assert.equal(metrics.reasonClass('universal-guard-plan-gate', 'Заблокировано план-гейтом: одобренного нет — реестр пуст.'), 'gate.empty');
  assert.equal(metrics.reasonClass('universal-guard-plan-gate', 'одобренное этого не покрывает — …'), 'gate.uncovered');
  assert.equal(metrics.reasonClass('universal-guard-plan-gate', 'это запрещено твоей же записью —'), 'gate.forbidden');
  assert.equal(metrics.reasonClass('universal-guard-plan-gate', 'сверка не дала решения'), 'gate.no-verdict');
  assert.equal(metrics.reasonClass('universal-guard-plan-gate', 'что-то новое'), 'gate.other');
  assert.equal(metrics.reasonClass('universal-guard-plan-delta', 'План повторяет уже одобренное'), 'delta.repeats');
  assert.equal(metrics.reasonClass('universal-sleep-waiter-guard', 'любой текст'), 'sleep-waiter-guard');
  assert.equal(metrics.reasonClass('craft-guard-markdown', ''), 'guard-markdown');
});

test('класс ответа модели: токен вердикта, json у разбора, unavailable у неответа', () => {
  assert.equal(metrics.verdictClass('COVERED Ц1.1: ок'), 'COVERED');
  assert.equal(metrics.verdictClass('ПОКРЫТА Ц1.1'), 'ПОКРЫТА');
  assert.equal(metrics.verdictClass('{"add":[]}'), 'json');
  assert.equal(metrics.verdictClass(''), 'unavailable');
  assert.equal(metrics.verdictClass('UNAVAILABLE'), 'unavailable');
});

test('токены хода: дубли одного message.id считаются однажды, смещение за последней полной строкой', () => {
  const file = path.join(FIXTURES, 'transcript-usage.jsonl');
  const first = metrics.turnUsage(file, 0);
  assert.deepEqual(first.usage, {
    input: 5, output: 140, cache_read: 2200, cache_create: 50, messages: 2,
  });
  assert.equal(first.offset, fs.statSync(file).size);
  const second = metrics.turnUsage(file, first.offset);
  assert.equal(second.usage.messages, 0, 'посчитанное второй раз не считается');
});

test('токены хода: недописанный хвост без перевода строки откладывается', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const file = path.join(dir, 't.jsonl');
  const line = JSON.stringify({ type: 'assistant', message: { id: 'm1', usage: { output_tokens: 7 } } });
  fs.writeFileSync(file, `${line}\n${line.slice(0, 20)}`);
  const r = metrics.turnUsage(file, 0);
  assert.equal(r.usage.output, 7);
  assert.equal(r.offset, Buffer.byteLength(`${line}\n`));
  fs.rmSync(dir, { recursive: true, force: true });
});

test('remote нормализуется до host/owner/repo', () => {
  assert.equal(metrics.normalizeRemote('https://github.com/Bonzar/craft.git'), 'github.com/Bonzar/craft');
  assert.equal(metrics.normalizeRemote('https://x-access-token:abc@github.com/Bonzar/craft'), 'github.com/Bonzar/craft');
  assert.equal(metrics.normalizeRemote('git@github.com:Bonzar/craft.git'), 'github.com/Bonzar/craft');
  assert.equal(metrics.normalizeRemote(''), '');
});

test('запись вызова модели без сессии и переопределения не делается', () => {
  delete process.env.CRAFT_METRICS_LOG;
  delete process.env.CLAUDE_CODE_SESSION_ID;
  const before = fs.existsSync('/tmp/metrics.default.jsonl') ? fs.statSync('/tmp/metrics.default.jsonl').size : 0;
  metrics.recordModelCall({ mode: 'cover', ms: 1, outcome: 'COVERED' });
  const after = fs.existsSync('/tmp/metrics.default.jsonl') ? fs.statSync('/tmp/metrics.default.jsonl').size : 0;
  assert.equal(after, before, 'общий журнал default не пополняется');
});

test('запись вызова модели идёт в переопределённый журнал', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  process.env.CRAFT_METRICS_LOG = path.join(dir, 'm.jsonl');
  metrics.recordModelCall({ mode: 'ingest', ms: 12, outcome: 'json' });
  const text = fs.readFileSync(process.env.CRAFT_METRICS_LOG, 'utf8');
  assert.match(text, /"kind":"model"/);
  assert.match(text, /"mode":"ingest","ms":12,"outcome":"json"/);
  delete process.env.CRAFT_METRICS_LOG;
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- сводка ------------------------------------------------------------------

const T = (sec) => new Date(Date.UTC(2026, 8, 2, 10, 0, sec)).toISOString();

test('сводка: ложный отказ — deny, затем тот же вызов прошёл в ходе после реплики', () => {
  const records = [
    { kind: 'session', ts: T(0), turn: 0, harness: 'claude', repo: 'github.com/Bonzar/craft', sid: 's' },
    { kind: 'prompt', ts: T(1), turn: 1, incident: false },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Edit', id: 'a', decision: 'deny', by: 'universal-guard-plan-gate', class: 'gate.uncovered', h: 'h1' },
    { kind: 'prompt', ts: T(3), turn: 2, incident: false },
    { kind: 'pre', ts: T(4), turn: 2, tool: 'Edit', id: 'b', decision: 'allow', by: '', class: '', h: 'h1' },
    { kind: 'post', ts: T(5), turn: 2, tool: 'Edit', id: 'b', error: false },
    { kind: 'stop', ts: T(6), turn: 2, blocked_by: '', usage: { input: 1, output: 2, cache_read: 3, cache_create: 4 } },
  ];
  const s = metrics.summarize(records, { sid: 's', now: Date.parse(T(7)) });
  assert.equal(s.false_denies, 1);
  assert.equal(s.denies.total, 1);
  assert.deepEqual(s.denies.by_class, { 'gate.uncovered': 1 });
  assert.equal(s.turns, 2);
  assert.equal(s.first_edit_ms, 5000, 'первая правка — post правящего инструмента без ошибки');
  assert.equal(s.started_at, T(0));
  assert.equal(s.repo, 'github.com/Bonzar/craft');
  assert.deepEqual(s.tokens, { input: 1, output: 2, cache_read: 3, cache_create: 4 });
});

test('сводка: тот же вызов, прошедший без реплики или кнопки, ложным отказом не считается', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Edit', id: 'a', decision: 'deny', class: 'gate.empty', h: 'h1' },
    { kind: 'pre', ts: T(3), turn: 1, tool: 'Edit', id: 'b', decision: 'allow', h: 'h1' },
  ];
  assert.equal(metrics.summarize(records).false_denies, 0);
});

test('сводка: ответ кнопкой снимает замок в том же ходе', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Bash', id: 'a', decision: 'deny', class: 'gate.uncovered', h: 'h1' },
    { kind: 'pre', ts: T(3), turn: 1, tool: 'AskUserQuestion', id: 'q', decision: 'allow', h: 'hq' },
    { kind: 'post', ts: T(4), turn: 1, tool: 'AskUserQuestion', id: 'q', error: false },
    { kind: 'pre', ts: T(5), turn: 1, tool: 'Bash', id: 'b', decision: 'allow', h: 'h1' },
  ];
  assert.equal(metrics.summarize(records).false_denies, 1);
});

test('сводка: циклы плана, инциденты, блокировки Stop, ошибки, исход', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1, incident: true },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Skill', id: 'k', decision: 'allow', skill: 'craft-incident', h: 'hk' },
    { kind: 'pre', ts: T(3), turn: 1, tool: 'ExitPlanMode', id: 'p1', decision: 'deny', class: 'delta.repeats', h: 'hp' },
    { kind: 'pre', ts: T(4), turn: 1, tool: 'ExitPlanMode', id: 'p2', decision: 'allow', h: 'hp' },
    { kind: 'post', ts: T(5), turn: 1, tool: 'ExitPlanMode', id: 'p2', error: false },
    { kind: 'pre', ts: T(6), turn: 1, tool: 'mcp__Craft__craft_write', id: 'w', decision: 'allow', h: 'hw' },
    { kind: 'post', ts: T(7), turn: 1, tool: 'mcp__Craft__craft_write', id: 'w', error: false },
    { kind: 'pre', ts: T(8), turn: 1, tool: 'Bash', id: 'g', decision: 'allow', h: 'hg', push: true },
    { kind: 'post', ts: T(9), turn: 1, tool: 'Bash', id: 'g', error: false },
    { kind: 'post', ts: T(10), turn: 1, tool: 'Bash', id: 'e', error: true },
    { kind: 'fail', ts: T(11), turn: 1, tool: 'Read', id: 'f' },
    { kind: 'model', ts: T(12), mode: 'cover', ms: 900, outcome: 'COVERED' },
    { kind: 'model', ts: T(13), mode: 'ingest', ms: 2000, outcome: 'json' },
    { kind: 'stop', ts: T(14), turn: 1, blocked_by: 'universal-stop-routine-facts', usage: {} },
    { kind: 'prompt', ts: T(15), turn: 2, incident: true },
    { kind: 'stop', ts: T(16), turn: 2, blocked_by: '', usage: {} },
  ];
  const s = metrics.summarize(records);
  assert.deepEqual(s.plan, { shown: 1, bounced: 1, approved: 1 });
  assert.deepEqual(s.incidents, { detected: 2, skill_called: 1, share: 0.5 });
  assert.deepEqual(s.stop_blocks, { 'universal-stop-routine-facts': 1 });
  assert.equal(s.tool_errors, 2);
  assert.deepEqual(s.outcome, { craft_writes: 1, pushed: true });
  assert.equal(s.model_calls.count, 2);
  assert.equal(s.model_calls.ms, 2900);
  assert.deepEqual(s.model_calls.by_mode, { cover: { count: 1, ms: 900 }, ingest: { count: 1, ms: 2000 } });
  assert.equal(s.first_edit_ms, 6000, 'первая правка — craft_write, план правкой не считается');
});

test('сводка: пустой журнал даёт пустую сводку без падения', () => {
  const s = metrics.summarize([]);
  assert.equal(s.turns, 0);
  assert.equal(s.tokens_first_turn, null);
  assert.equal(s.incidents.share, null);
});

// --- сигналы -----------------------------------------------------------------

test('хеш реплики не зависит от регистра, пробелов и знаков препинания', () => {
  assert.equal(metrics.promptHash('Убери хвосты, из README!'), metrics.promptHash('убери   хвосты из readme'));
  assert.notEqual(metrics.promptHash('убери хвосты'), metrics.promptHash('добавь хвосты'));
  assert.equal(metrics.promptHash('   '), '');
});

test('маркеры переуказания', () => {
  assert.equal(metrics.looksLikeReinstruction('я же просил не трогать README'), true);
  assert.equal(metrics.looksLikeReinstruction('Ещё раз: без хвостов'), true);
  assert.equal(metrics.looksLikeReinstruction('опять то же самое'), true);
  assert.equal(metrics.looksLikeReinstruction('поправь README'), false);
  assert.equal(metrics.looksLikeReinstruction('сноваяркий'), false, 'маркер внутри слова не считается');
});

test('сводка: сигналы складываются из признаков событий', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1, repeat: false, reinstruct: true },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Bash', id: 'a', decision: 'allow', h: 'h1' },
    { kind: 'post', ts: T(3), turn: 1, tool: 'Bash', id: 'a', error: true },
    { kind: 'pre', ts: T(4), turn: 1, tool: 'Bash', id: 'b', decision: 'allow', h: 'h1', repeat_call: true },
    { kind: 'post', ts: T(5), turn: 1, tool: 'Bash', id: 'b', error: true },
    { kind: 'pre', ts: T(6), turn: 1, tool: 'ExitPlanMode', id: 'p', decision: 'allow', h: 'hp', stage_repeat: true },
    { kind: 'post', ts: T(7), turn: 1, tool: 'ExitPlanMode', id: 'p', error: false },
    { kind: 'stop', ts: T(8), turn: 1, blocked_by: '', usage: {}, no_progress: false, tool_errors: 2 },
    { kind: 'prompt', ts: T(9), turn: 2, repeat: true, reinstruct: false },
    { kind: 'stop', ts: T(10), turn: 2, blocked_by: '', usage: {}, no_progress: true, tool_errors: 0 },
  ];
  const s = metrics.summarize(records);
  assert.deepEqual(s.signals, {
    reinstructions: 2, call_repeats: 1, stage_repeats: 1, turns_without_progress: 1, error_streak_max: 2,
  });
});

test('сводка: ход без прогресса считается по ходу, по последнему его Stop', () => {
  const blockedThenDone = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'stop', ts: T(2), turn: 1, blocked_by: 'universal-stop-routine-facts', usage: {}, no_progress: true },
    { kind: 'stop', ts: T(3), turn: 1, blocked_by: '', usage: {}, no_progress: false },
  ];
  assert.equal(metrics.summarize(blockedThenDone).signals.turns_without_progress, 0, 'ход в итоге дал прогресс');
  const stuck = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'stop', ts: T(2), turn: 1, blocked_by: 'universal-stop-routine-facts', usage: {}, no_progress: true },
    { kind: 'stop', ts: T(3), turn: 1, blocked_by: '', usage: {}, no_progress: true },
  ];
  assert.equal(metrics.summarize(stuck).signals.turns_without_progress, 1, 'два Stop одного хода — один ход');
});

test('прогресс: удавшаяся мутация через Bash или чужой MCP-инструмент записи', () => {
  assert.equal(metrics.looksMutating('Bash', { command: 'echo x >> README.md' }), true);
  assert.equal(metrics.looksMutating('Bash', { command: 'git commit -m x' }), true);
  assert.equal(metrics.looksMutating('Bash', { command: 'ls -la' }), false);
  assert.equal(metrics.looksMutating('Bash', { command: 'echo x > /tmp/scratch.txt' }), false, 'эфемерная цель — не мутация');
  assert.equal(metrics.looksMutating('mcp__github__create_pull_request', {}), true);
  assert.equal(metrics.looksMutating('mcp__github__list_pull_requests', {}), false);
  assert.equal(metrics.looksMutating('Edit', { file_path: '/home/user/craft/README.md' }), true);
  assert.equal(metrics.looksMutating('Edit', { file_path: '/tmp/scratch.txt' }), false,
    'правка эфемерной цели прогрессом не считается — как и запись в неё через шелл');
  assert.equal(metrics.looksMutating('Edit', {}), false, 'правка без цели мир не меняет');
  assert.equal(metrics.looksMutating('Read', {}), false);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: true }, { error: false }), true);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: true }, { error: true }), false);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: false }, { error: false }), false);
});

// --- журнал по событию -----------------------------------------------------------

test('вызов модели ложится в журнал сессии из события, когда переменной сессии нет', () => {
  delete process.env.CRAFT_METRICS_LOG;
  delete process.env.CLAUDE_CODE_SESSION_ID;
  const sid = `unit-${process.pid}-${Date.now()}`;
  globalThis.hookEvent = { session_id: sid };
  const log = `/tmp/metrics.${sid}.jsonl`;
  try {
    metrics.recordModelCall({ mode: 'cover', ms: 3, outcome: 'COVERED' });
    assert.match(fs.readFileSync(log, 'utf8'), /"kind":"model","ts":".*","mode":"cover","ms":3/);
    assert.equal(metrics.childEnv({}).CRAFT_METRICS_LOG, log, 'дочерний процесс получает журнал явно');
  } finally {
    delete globalThis.hookEvent;
    fs.rmSync(log, { force: true });
  }
});

test('фоновый приём пишет вызов модели в журнал сессии события', async () => {
  delete process.env.CRAFT_METRICS_LOG;
  delete process.env.CLAUDE_CODE_SESSION_ID;
  const sid = `unit-ingest-${process.pid}-${Date.now()}`;
  globalThis.hookEvent = { session_id: sid };
  const log = `/tmp/metrics.${sid}.jsonl`;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const repo = path.resolve(HERE, '..', '..');
  process.env.PLAN_CLASSIFIER_CMD = path.join(repo, 'tests', 'hooks', 'fixtures', 'mock-classifier.sh');
  process.env.CRAFT_REGISTRY_SYNC = '1';
  try {
    const registry = await import(`../../.claude/hooks/lib/registry.js?t=${Date.now()}`);
    registry.ingestInBackground(path.join(dir, 'registry.jsonl'), 'reply', 'поправь README');
    assert.match(fs.readFileSync(log, 'utf8'), /"kind":"model".*"mode":"ingest"/);
  } finally {
    delete globalThis.hookEvent;
    delete process.env.PLAN_CLASSIFIER_CMD;
    delete process.env.CRAFT_REGISTRY_SYNC;
    fs.rmSync(log, { force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('проектная регистрация диспетчера находится вверх от рабочего каталога', () => {
  const repo = path.resolve(HERE, '..', '..');
  assert.equal(metrics.projectDispatcherAt(path.join(repo, 'tests', 'hooks')), true);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  assert.equal(metrics.projectDispatcherAt(dir), false);
  assert.equal(metrics.projectDispatcherAt(''), false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('сводка: повторный старт (компакт) не двигает начало сессии', () => {
  const records = [
    { kind: 'session', ts: T(0), turn: 0, harness: 'claude', repo: 'r', sid: 's', source: 'startup' },
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'session', ts: T(5), turn: 1, harness: 'claude', repo: 'r', sid: 's', source: 'compact' },
    { kind: 'prompt', ts: T(6), turn: 2 },
  ];
  const s = metrics.summarize(records);
  assert.equal(s.started_at, T(0));
  assert.equal(s.turns, 2);
});

test('сводка: токены первого хода складываются из всех его Stop', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'stop', ts: T(2), turn: 1, blocked_by: 'universal-stop-routine-facts', usage: { input: 1, output: 10, cache_read: 0, cache_create: 0 } },
    { kind: 'stop', ts: T(3), turn: 1, blocked_by: '', usage: { input: 2, output: 20, cache_read: 5, cache_create: 0 } },
    { kind: 'prompt', ts: T(4), turn: 2 },
    { kind: 'stop', ts: T(5), turn: 2, blocked_by: '', usage: { input: 100, output: 100, cache_read: 100, cache_create: 100 } },
  ];
  const s = metrics.summarize(records);
  assert.deepEqual(s.tokens_first_turn, { input: 3, output: 30, cache_read: 5, cache_create: 0 });
  assert.deepEqual(s.tokens, { input: 103, output: 130, cache_read: 105, cache_create: 100 });
});

// --- состояние под локом ---------------------------------------------------------

test('параллельные правки состояния не теряют вызовов в полёте', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const log = path.join(dir, 'm.jsonl');
  const hook = path.resolve(HERE, '..', '..', '.claude', 'hooks', 'universal-metrics.js');
  const { execFile } = await import('node:child_process');

  // Восемь вызовов инструмента, поданных разом, — обычная пачка от харнесса.
  const calls = Array.from({ length: 8 }, (_, i) => `toolu_par_${i}`);
  await Promise.all(calls.map((id) => new Promise((done) => {
    const child = execFile(process.execPath, [hook], {
      env: { ...process.env, CRAFT_METRICS_LOG: log, HOOK_ONCE: 'off' },
    }, () => done());
    child.stdin.end(JSON.stringify({
      hook_event_name: 'PreToolUse', session_id: 'par', tool_name: 'Read', tool_use_id: id,
      tool_input: { file_path: 'README.md' },
    }));
  })));

  const state = JSON.parse(fs.readFileSync(`${log}.state.json`, 'utf8'));
  assert.deepEqual(Object.keys(state.inflight).sort(), calls.sort(), 'ни один вызов в полёте не потерян');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('смещение транскрипта засевается длиной файла: хвост до старта в ход не идёт', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const transcript = path.join(dir, 't.jsonl');
  fs.copyFileSync(path.join(FIXTURES, 'transcript-usage.jsonl'), transcript);
  assert.equal(metrics.transcriptSize(transcript), fs.statSync(transcript).size);
  const { usage } = metrics.turnUsage(transcript, metrics.transcriptSize(transcript));
  assert.equal(usage.messages, 0, 'засеянное смещение не даёт засчитать историю в первый ход');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('укоротившийся транскрипт читается заново, а не молчит навсегда', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const file = path.join(dir, 't.jsonl');
  const line = JSON.stringify({ type: 'assistant', message: { id: 'm1', usage: { output_tokens: 9 } } });
  fs.writeFileSync(file, `${line}\n`);
  const r = metrics.turnUsage(file, 10_000); // смещение больше файла: транскрипт подменён
  assert.equal(r.usage.output, 9, 'после подмены транскрипт читается с начала');
  assert.equal(r.offset, Buffer.byteLength(`${line}\n`));
  fs.rmSync(dir, { recursive: true, force: true });
});

test('remote с портом не превращает порт во владельца', () => {
  assert.equal(metrics.normalizeRemote('ssh://git@github.com:2222/Bonzar/craft.git'), 'github.com/Bonzar/craft');
  assert.equal(metrics.normalizeRemote('https://github.com:443/Bonzar/craft.git'), 'github.com/Bonzar/craft');
  assert.equal(metrics.normalizeRemote('git@github.com:Bonzar/craft.git'), 'github.com/Bonzar/craft', 'scp-форма цела');
});

test('вызов с аварийным выключателем в счётчик вызовов модели не идёт', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const log = path.join(dir, 'm.jsonl');
  process.env.CRAFT_METRICS_LOG = log;
  const repo = path.resolve(HERE, '..', '..');
  const bin = path.join(repo, 'tests', 'hooks', 'fixtures', 'mock-classifier.sh');
  const classifier = await import(`../../.claude/hooks/lib/classifier.js?t=${Date.now()}`);
  try {
    process.env.PLAN_CLASSIFIER = 'off';
    classifier.classify(bin, 'cover', [], 'проба');
    assert.equal(fs.existsSync(log), false, 'выключенный классификатор модель не звал — записи нет');
    delete process.env.PLAN_CLASSIFIER;
    classifier.classify(bin, 'cover', [], 'проба');
    assert.match(fs.readFileSync(log, 'utf8'), /"kind":"model"/, 'обычный вызов пишется');
  } finally {
    delete process.env.PLAN_CLASSIFIER;
    delete process.env.CRAFT_METRICS_LOG;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// --- узнавание вызова и пуша -----------------------------------------------------

test('хеш вызова не зависит от служебных полей входа', () => {
  const a = metrics.callHash('Bash', { command: 'git push', description: 'Push branch', timeout: 120000 });
  const b = metrics.callHash('Bash', { command: 'git push', description: 'Push the branch to origin' });
  assert.equal(a, b, 'переписанное описание не должно делать повтор другим вызовом');
  assert.notEqual(a, metrics.callHash('Bash', { command: 'git status' }));
  assert.equal(
    metrics.callHash('Edit', { file_path: 'a', old_string: 'x', new_string: 'y' }),
    metrics.callHash('Edit', { new_string: 'y', old_string: 'x', file_path: 'a' }),
    'порядок полей во входе не обещан — хеш от него не зависит',
  );
});

test('пуш опознаётся по подкоманде, а не по слову в строке', () => {
  assert.equal(metrics.looksLikePush('git push -u origin main'), true);
  assert.equal(metrics.looksLikePush('git push'), true);
  assert.equal(metrics.looksLikePush('git stash push -m wip'), false);
  assert.equal(metrics.looksLikePush('git commit -m "fix push hook"'), false);
  assert.equal(metrics.looksLikePush('git log --grep push'), false);
  assert.equal(metrics.looksLikePush('git push --dry-run'), false, 'пробный прогон не пуш');
});

test('журнал: нет файла — пусто, не прочитался — null', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  assert.deepEqual(metrics.readJournal(path.join(dir, 'нет.jsonl')), []);
  assert.equal(metrics.readJournal(dir), null, 'каталог вместо файла — не пустой журнал, а сбой чтения');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('сводка: отказ, снятый ходами позже, ложным не считается', () => {
  const base = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Edit', id: 'a', decision: 'deny', class: 'gate.uncovered', h: 'h1' },
  ];
  const next = metrics.summarize([...base,
    { kind: 'prompt', ts: T(3), turn: 2 },
    { kind: 'pre', ts: T(4), turn: 2, tool: 'Edit', id: 'b', decision: 'allow', h: 'h1' },
  ]);
  assert.equal(next.false_denies, 1, 'ход сразу после реплики — это и есть ложный отказ');

  const later = metrics.summarize([...base,
    { kind: 'prompt', ts: T(3), turn: 2 },
    { kind: 'prompt', ts: T(4), turn: 3 },
    { kind: 'prompt', ts: T(5), turn: 4 },
    { kind: 'pre', ts: T(6), turn: 4, tool: 'Edit', id: 'b', decision: 'allow', h: 'h1' },
  ]);
  assert.equal(later.false_denies, 0, 'через три хода это уже не «сразу после реплики», а новая работа');
});

// --- сигналы по ревью ------------------------------------------------------------

test('короткое подтверждение повтором не считается', () => {
  assert.equal(metrics.promptHash('ок'), '', 'ниже порога хеша нет — значит нет и повтора');
  assert.equal(metrics.promptHash('да, продолжай'), metrics.promptHash('Да, продолжай!'));
  assert.notEqual(metrics.promptHash('убери хвосты из readme'), '');
});

test('вежливый оборот с тем же маркером переуказанием не считается', () => {
  assert.equal(metrics.looksLikeReinstruction('Ещё раз спасибо!'), false);
  assert.equal(metrics.looksLikeReinstruction('и снова здравствуйте'), false);
  assert.equal(metrics.looksLikeReinstruction('я же просил не трогать README'), true);
  assert.equal(metrics.looksLikeReinstruction('Ещё раз: без хвостов'), true);
});

test('вежливая поправка остаётся переуказанием', () => {
  assert.equal(metrics.looksLikeReinstruction('Спасибо, но я же просил не трогать README'), true,
    'вежливый зачин вычёркивается, признак считается по остатку');
  assert.equal(metrics.looksLikeReinstruction('Привет! Опять ты трогаешь README'), true);
  assert.equal(metrics.looksLikeReinstruction('благодарю, снова забыл про тесты'), true);
  assert.equal(metrics.looksLikeReinstruction('спасибо!'), false, 'чистая вежливость остатка не оставляет');
  assert.equal(metrics.looksLikeReinstruction('Добрый день'), false);
});

test('читающие команды гита мутацией не считаются', () => {
  for (const cmd of ['git diff --merge-base main', 'git log --oneline -- lib/tag.js',
    'git show HEAD:src/reset.js', 'git stash list', 'git stash show', 'git tag',
    'git tag --list', "git tag -l 'v*'", 'git branch', 'git branch -a', 'git branch -r',
    'git remote', 'git remote -v', 'git remote show origin', 'git worktree list',
    'git worktree list', 'git status']) {
    assert.equal(metrics.looksMutating('Bash', { command: cmd }), false, cmd);
  }
  for (const cmd of ['git commit -m x', 'git -C /repo push origin main', 'git stash push -m wip',
    'git stash', 'git tag v1', 'git checkout -b feature',
    // Правящие формы тех же подкоманд и сетевые вызовы: они пишут ссылки и
    // объекты в локальный репозиторий, и ход с ними ходом без изменений не был.
    'git fetch origin', 'git pull', 'git branch feature', 'git branch -d old',
    'git remote add origin https://example.invalid/r.git', 'git worktree add /tmp/w']) {
    assert.equal(metrics.looksMutating('Bash', { command: cmd }), true, cmd);
  }
});

// В цепочке смотрятся ВСЕ вызовы: по одному первому «git status && git commit»
// читался бы как ход без единого изменения, а слово в кавычках — как правка.
test('правка видна в любом месте цепочки, а слово в кавычках правкой не считается', () => {
  assert.equal(metrics.looksMutating('Bash', { command: 'git status && git commit -m x' }), true);
  assert.equal(metrics.looksMutating('Bash', { command: 'git log --oneline | head; git tag v1' }), true);
  assert.equal(metrics.looksMutating('Bash', { command: "printf 'git commit -m x'" }), false);
  assert.equal(metrics.looksMutating('Bash', { command: 'echo git push' }), false);
});

test('работа через подагента считается прогрессом, чтение — нет', () => {
  assert.equal(metrics.looksMutating('Task', { subagent_type: 'general-purpose' }), true);
  assert.equal(metrics.looksMutating('Task', { subagent_type: 'Explore' }), false, 'разведка мир не трогает');
  assert.equal(metrics.looksMutating('mcp__Claude_Code_Remote__subscribe_pr_activity', {}), false,
    'обслуживание своего хода — не правка мира, как и у гейта');
});

test('сводка: серия ошибок не переходит через реплику Влада', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'post', ts: T(2), turn: 1, tool: 'Bash', id: 'a', error: true },
    { kind: 'post', ts: T(3), turn: 1, tool: 'Bash', id: 'b', error: true },
    { kind: 'prompt', ts: T(4), turn: 2 },
    { kind: 'post', ts: T(5), turn: 2, tool: 'Bash', id: 'c', error: true },
  ];
  assert.equal(metrics.summarize(records).signals.error_streak_max, 2,
    'три ошибки, но между второй и третьей — вмешательство Влада');
});

// Пуш опознаётся по вызову git, а не по слову в строке: `printf 'git push'`
// пушем не является, и исход сессии на нём не помечается пушем.
test('пуш: вызов git, а не слово где угодно в команде', () => {
  assert.equal(metrics.looksLikePush('git push -u origin main'), true);
  assert.equal(metrics.looksLikePush('cd /repo && git push'), true);
  assert.equal(metrics.looksLikePush('git -C /repo push'), true);
  assert.equal(metrics.looksLikePush("printf 'git push'"), false, 'слово в кавычках командой не является');
  assert.equal(metrics.looksLikePush('echo git push'), false, 'аргумент чужой команды');
  assert.equal(metrics.looksLikePush('git commit -m "fix push"'), false);
  assert.equal(metrics.looksLikePush('git stash push'), false);
  assert.equal(metrics.looksLikePush('git push --dry-run'), false);
});

// Хеш вызова канонизируется на ВСЕХ уровнях: порядок полей во вложенном объекте
// смысла не несёт, а на разном хеше повтор после отказа не узнавался и ложный
// отказ не засчитывался. Порядок элементов массива, наоборот, значим.
test('хеш вызова: вложенные поля сортируются, порядок массива значим', () => {
  const a = { file_path: '/a', edits: [{ old_string: 'x', new_string: 'y' }] };
  const b = { edits: [{ new_string: 'y', old_string: 'x' }], file_path: '/a' };
  assert.equal(metrics.callHash('MultiEdit', a), metrics.callHash('MultiEdit', b));
  assert.notEqual(
    metrics.callHash('MultiEdit', { edits: [{ t: 1 }, { t: 2 }] }),
    metrics.callHash('MultiEdit', { edits: [{ t: 2 }, { t: 1 }] }),
  );
});

// Фоновый приём реестра кончается ПОЗЖЕ последнего Stop хода: его вызов модели
// приходит в журнал, когда сводка уже сложена. Без пересборки такой вызов не
// попадал ни в одну сводку, и хранение увозило сессию с недосчитанными
// вызовами модели.
test('вызов модели после сводки пересобирает её копию', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-model-'));
  const log = path.join(dir, 'm.jsonl');
  metrics.append(log, { kind: 'session', ts: new Date().toISOString(), turn: 0 });
  metrics.refreshSummary(log, { sid: 'm-sid', record: true });
  const before = JSON.parse(fs.readFileSync(`${log}.summary.json`, 'utf8'));
  assert.equal(before.model_calls.count, 0, 'предусловие: сводка сложена и вызовов в ней нет');

  const saved = process.env.CRAFT_METRICS_LOG;
  process.env.CRAFT_METRICS_LOG = log;
  try {
    metrics.recordModelCall({ mode: 'ingest', ms: 1200, outcome: 'json' });
  } finally {
    if (saved === undefined) delete process.env.CRAFT_METRICS_LOG;
    else process.env.CRAFT_METRICS_LOG = saved;
  }

  const after = JSON.parse(fs.readFileSync(`${log}.summary.json`, 'utf8'));
  assert.equal(after.model_calls.count, 1, 'вызов из фонового процесса дошёл до копии сводки');
  assert.equal(after.model_calls.ms, 1200);
  assert.equal(after.model_calls.by_mode.ingest.count, 1);
});
