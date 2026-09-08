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
const tokens = await import('../../.claude/hooks/lib/usage-claude.js');
const registration = await import('../../.claude/hooks/lib/registration-claude.js');
const hash = await import('../../.claude/hooks/lib/call-hash.js');
const reason = await import('../../.claude/hooks/lib/reason-class.js');
const { summarize } = await import('../../.claude/hooks/lib/metrics-summary.js');

test('класс причины: план-гейт по тексту, остальные по имени хука', () => {
  assert.equal(reason.reasonClass('universal-guard-plan-gate', 'Заблокировано план-гейтом: одобренного нет — реестр пуст.'), 'gate.empty');
  assert.equal(reason.reasonClass('universal-guard-plan-gate', 'одобренное этого не покрывает — …'), 'gate.uncovered');
  assert.equal(reason.reasonClass('universal-guard-plan-gate', 'это запрещено твоей же записью —'), 'gate.forbidden');
  assert.equal(reason.reasonClass('universal-guard-plan-gate', 'сверка не дала решения'), 'gate.no-verdict');
  assert.equal(reason.reasonClass('universal-guard-plan-gate', 'что-то новое'), 'gate.other');
  assert.equal(reason.reasonClass('universal-guard-plan-delta', 'План повторяет уже одобренное'), 'delta.repeats');
  assert.equal(reason.reasonClass('universal-fact-gate', 'любой текст'), 'fact-gate');
  assert.equal(reason.reasonClass('craft-guard-markdown', ''), 'guard-markdown');
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
  const first = tokens.turnUsage(file, 0);
  assert.deepEqual(first.usage, {
    input: 5, output: 140, cache_read: 2200, cache_create: 50, messages: 2,
  });
  assert.equal(first.offset, fs.statSync(file).size);
  const second = tokens.turnUsage(file, first.offset);
  assert.equal(second.usage.messages, 0, 'посчитанное второй раз не считается');
});

test('токены хода: недописанный хвост без перевода строки откладывается', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const file = path.join(dir, 't.jsonl');
  const line = JSON.stringify({ type: 'assistant', message: { id: 'm1', usage: { output_tokens: 7 } } });
  fs.writeFileSync(file, `${line}\n${line.slice(0, 20)}`);
  const r = tokens.turnUsage(file, 0);
  assert.equal(r.usage.output, 7);
  assert.equal(r.offset, Buffer.byteLength(`${line}\n`));
  fs.rmSync(dir, { recursive: true, force: true });
});

test('запись вызова модели без сессии и переопределения не делается', () => {
  delete process.env.CRAFT_METRICS_LOG;
  delete process.env.CLAUDE_CODE_SESSION_ID;
  // Путь строит сам модуль — от os.tmpdir(), а не от зашитого /tmp: с
  // подменённым TMPDIR кейс иначе смотрел бы не на тот файл.
  const shared = path.join(os.tmpdir(), 'metrics.default.jsonl');
  const sizeOf = () => (fs.existsSync(shared) ? fs.statSync(shared).size : 0);
  const before = sizeOf();
  metrics.recordModelCall({ mode: 'cover', ms: 1, outcome: 'COVERED' });
  const after = sizeOf();
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
    { kind: 'pre', ts: T(4), turn: 2, tool: 'Edit', id: 'b', decision: 'allow', by: '', class: '', h: 'h1', edit: true },
    { kind: 'post', ts: T(5), turn: 2, tool: 'Edit', id: 'b', error: false },
    { kind: 'stop', ts: T(6), turn: 2, blocked_by: '', usage: { input: 1, output: 2, cache_read: 3, cache_create: 4 } },
  ];
  const s = summarize(records, { sid: 's', now: Date.parse(T(7)) });
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
  assert.equal(summarize(records).false_denies, 0);
});

test('сводка: ответ кнопкой снимает замок в том же ходе', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Bash', id: 'a', decision: 'deny', class: 'gate.uncovered', h: 'h1' },
    { kind: 'pre', ts: T(3), turn: 1, tool: 'AskUserQuestion', id: 'q', decision: 'allow', h: 'hq', question: true, stage: true },
    { kind: 'post', ts: T(4), turn: 1, tool: 'AskUserQuestion', id: 'q', error: false },
    { kind: 'pre', ts: T(5), turn: 1, tool: 'Bash', id: 'b', decision: 'allow', h: 'h1' },
  ];
  assert.equal(summarize(records).false_denies, 1);
});

test('сводка: циклы плана, инциденты, блокировки Stop, ошибки, исход', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1, incident: true },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Skill', id: 'k', decision: 'allow', incident_skill: true, h: 'hk' },
    { kind: 'post', ts: T(2), turn: 1, tool: 'Skill', id: 'k', error: false },
    { kind: 'pre', ts: T(3), turn: 1, tool: 'ExitPlanMode', id: 'p1', decision: 'deny', class: 'delta.repeats', h: 'hp', plan: true, stage: true },
    { kind: 'pre', ts: T(4), turn: 1, tool: 'ExitPlanMode', id: 'p2', decision: 'allow', h: 'hp', plan: true, stage: true },
    { kind: 'post', ts: T(5), turn: 1, tool: 'ExitPlanMode', id: 'p2', error: false },
    { kind: 'pre', ts: T(6), turn: 1, tool: 'mcp__Craft__craft_write', id: 'w', decision: 'allow', h: 'hw', edit: true, note_write: true },
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
  const s = summarize(records);
  assert.deepEqual(s.plan, { shown: 1, bounced: 1, approved: 1 });
  assert.deepEqual(s.incidents, { detected: 2, skill_called: 1, share: 0.5 });
  assert.deepEqual(s.stop_blocks, { 'universal-stop-routine-facts': 1 });
  assert.equal(s.tool_errors, 2);
  assert.deepEqual(s.outcome, { note_writes: 1, pushed: true });
  assert.equal(s.model_calls.count, 2);
  assert.equal(s.model_calls.ms, 2900);
  assert.deepEqual(s.model_calls.by_mode, { cover: { count: 1, ms: 900 }, ingest: { count: 1, ms: 2000 } });
  assert.equal(s.first_edit_ms, 6000, 'первая правка — запись в базу заметок, план правкой не считается');
});

test('сводка: пустой журнал даёт пустую сводку без падения', () => {
  const s = summarize([]);
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

test('сводка: сигналы складываются из признаков событий', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1, repeat: false, reinstruct: true },
    { kind: 'pre', ts: T(2), turn: 1, tool: 'Bash', id: 'a', decision: 'allow', h: 'h1' },
    { kind: 'post', ts: T(3), turn: 1, tool: 'Bash', id: 'a', error: true },
    { kind: 'pre', ts: T(4), turn: 1, tool: 'Bash', id: 'b', decision: 'allow', h: 'h1', repeat_call: true },
    { kind: 'post', ts: T(5), turn: 1, tool: 'Bash', id: 'b', error: true },
    { kind: 'pre', ts: T(6), turn: 1, tool: 'ExitPlanMode', id: 'p', decision: 'allow', h: 'hp', stage_repeat: true },
    { kind: 'post', ts: T(7), turn: 1, tool: 'ExitPlanMode', id: 'p', error: false },
    { kind: 'stop', ts: T(8), turn: 1, blocked_by: '', usage: {}, no_progress: false },
    { kind: 'prompt', ts: T(9), turn: 2, repeat: true, reinstruct: false },
    { kind: 'stop', ts: T(10), turn: 2, blocked_by: '', usage: {}, no_progress: true },
  ];
  const s = summarize(records);
  // Повтор реплики и словесный маркер — РАЗНЫЕ признаки: в одном счётчике по
  // сводке нельзя было сказать, чего именно было больше.
  assert.deepEqual(s.signals, {
    prompt_repeats: 1,
    reinstructions: 1,
    call_repeats: 1,
    stage_repeats: 1,
    turns_without_progress: 1,
    error_streak_max: 2,
  });
  assert.equal(s.tool_errors, 2, 'ошибки инструментов считает сводка, а запись Stop такого поля не несёт');
});

test('сводка: ход без прогресса считается по ходу, по последнему его Stop', () => {
  const blockedThenDone = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'stop', ts: T(2), turn: 1, blocked_by: 'universal-stop-routine-facts', usage: {}, no_progress: true },
    { kind: 'stop', ts: T(3), turn: 1, blocked_by: '', usage: {}, no_progress: false },
  ];
  assert.equal(summarize(blockedThenDone).signals.turns_without_progress, 0, 'ход в итоге дал прогресс');
  const stuck = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'stop', ts: T(2), turn: 1, blocked_by: 'universal-stop-routine-facts', usage: {}, no_progress: true },
    { kind: 'stop', ts: T(3), turn: 1, blocked_by: '', usage: {}, no_progress: true },
  ];
  assert.equal(summarize(stuck).signals.turns_without_progress, 1, 'два Stop одного хода — один ход');
});

// --- журнал по событию -----------------------------------------------------------

test('вызов модели ложится в журнал сессии, которую положила обёртка', () => {
  delete process.env.CRAFT_METRICS_LOG;
  // Сессию в окружение кладёт АДАПТЕР события, прочитав её из самого события;
  // общая часть берёт её уже под своим именем и имени переменной харнеса не знает.
  const sid = `unit-${process.pid}-${Date.now()}`;
  process.env.CRAFT_SESSION_ID = sid;
  const log = path.join(os.tmpdir(), `metrics.${sid}.jsonl`);
  try {
    metrics.recordModelCall({ mode: 'cover', ms: 3, outcome: 'COVERED' });
    assert.match(fs.readFileSync(log, 'utf8'), /"kind":"model","ts":".*","mode":"cover","ms":3/);
    assert.equal(metrics.childEnv({}).CRAFT_METRICS_LOG, log, 'дочерний процесс получает журнал явно');
  } finally {
    delete process.env.CRAFT_SESSION_ID;
    fs.rmSync(log, { force: true });
  }
});

test('фоновый приём пишет вызов модели в журнал сессии события', async () => {
  delete process.env.CRAFT_METRICS_LOG;
  const sid = `unit-ingest-${process.pid}-${Date.now()}`;
  process.env.CRAFT_SESSION_ID = sid;
  const log = path.join(os.tmpdir(), `metrics.${sid}.jsonl`);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const repo = path.resolve(HERE, '..', '..');
  process.env.PLAN_CLASSIFIER_CMD = path.join(repo, 'tests', 'hooks', 'fixtures', 'mock-classifier.sh');
  process.env.CRAFT_REGISTRY_SYNC = '1';
  // Приём кончается возвратом сводки в очередь хранения. Кейс про журнал, а не
  // про хранение, и гасит его ЯВНО: сегодня очередь не заводится лишь потому,
  // что сводки по этому пути нет, — то есть защита держится на том, что кейс не
  // станет её складывать.
  const savedStore = process.env.METRICS_STORE;
  process.env.METRICS_STORE = 'off';
  try {
    const registry = await import(`../../.claude/hooks/lib/registry.js?t=${Date.now()}`);
    registry.ingestInBackground(path.join(dir, 'registry.jsonl'), 'reply', 'поправь README');
    assert.match(fs.readFileSync(log, 'utf8'), /"kind":"model".*"mode":"ingest"/);
  } finally {
    delete process.env.CRAFT_SESSION_ID;
    delete process.env.PLAN_CLASSIFIER_CMD;
    delete process.env.CRAFT_REGISTRY_SYNC;
    if (savedStore === undefined) delete process.env.METRICS_STORE;
    else process.env.METRICS_STORE = savedStore;
    fs.rmSync(log, { force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('проектная регистрация диспетчера находится вверх от рабочего каталога', () => {
  const repo = path.resolve(HERE, '..', '..');
  assert.equal(registration.projectDispatcherAt(path.join(repo, 'tests', 'hooks')), true);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  assert.equal(registration.projectDispatcherAt(dir), false);
  assert.equal(registration.projectDispatcherAt(''), false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('сводка: повторный старт (компакт) не двигает начало сессии', () => {
  const records = [
    { kind: 'session', ts: T(0), turn: 0, harness: 'claude', repo: 'r', sid: 's', source: 'startup' },
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'session', ts: T(5), turn: 1, harness: 'claude', repo: 'r', sid: 's', source: 'compact' },
    { kind: 'prompt', ts: T(6), turn: 2 },
  ];
  const s = summarize(records);
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
  const s = summarize(records);
  assert.deepEqual(s.tokens_first_turn, { input: 3, output: 30, cache_read: 5, cache_create: 0 });
  assert.deepEqual(s.tokens, { input: 103, output: 130, cache_read: 105, cache_create: 100 });
});

// --- состояние под локом ---------------------------------------------------------

// Восемь параллельных правок состояния под одним локом. Проверяется КОНТРАКТ:
// вызов либо попал в состояние, либо пропуск НАЗВАН строкой журнала — молча не
// теряется ни один. Требовать, чтобы выжили все восемь, нельзя: лок ждут 300 мс
// (`STATE_WAIT_MS`), потому что хук стоит в цепочке хода, и на загруженной машине
// последний из восьми в этот срок законно не укладывается — на раннере CI так и
// вышло, двое из восьми ушли в пропуск. «Ждать дольше» здесь означало бы, что
// ждёт Влад.
test('параллельные правки состояния не теряют вызовов в полёте молча', async () => {
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
  const inflight = Object.keys(state.inflight).sort();
  const skips = fs.readFileSync(log, 'utf8').trim().split('\n')
    .map((line) => JSON.parse(line))
    .filter((rec) => rec.kind === 'skip' && rec.what === 'state');

  for (const id of inflight) assert.ok(calls.includes(id), `чужой вызов в состоянии: ${id}`);
  assert.ok(inflight.length > 0, 'хотя бы один вызов записан: иначе лок не отдаётся вовсе');
  assert.equal(inflight.length + skips.length, calls.length,
    'каждый вызов либо в состоянии, либо назван пропуском — молча не теряется ни один');
  for (const skip of skips) assert.equal(skip.wait_ms, 300, 'пропуск называет срок, на котором сдался');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('смещение транскрипта засевается длиной файла: хвост до старта в ход не идёт', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const transcript = path.join(dir, 't.jsonl');
  fs.copyFileSync(path.join(FIXTURES, 'transcript-usage.jsonl'), transcript);
  assert.equal(tokens.transcriptSize(transcript), fs.statSync(transcript).size);
  const { usage } = tokens.turnUsage(transcript, tokens.transcriptSize(transcript));
  assert.equal(usage.messages, 0, 'засеянное смещение не даёт засчитать историю в первый ход');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('укоротившийся транскрипт читается заново, а не молчит навсегда', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const file = path.join(dir, 't.jsonl');
  const line = JSON.stringify({ type: 'assistant', message: { id: 'm1', usage: { output_tokens: 9 } } });
  fs.writeFileSync(file, `${line}\n`);
  const r = tokens.turnUsage(file, 10_000); // смещение больше файла: транскрипт подменён
  assert.equal(r.usage.output, 9, 'после подмены транскрипт читается с начала');
  assert.equal(r.offset, Buffer.byteLength(`${line}\n`));
  fs.rmSync(dir, { recursive: true, force: true });
});

test('вызов с аварийным выключателем в счётчик вызовов модели не идёт', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-test.'));
  const log = path.join(dir, 'm.jsonl');
  process.env.CRAFT_METRICS_LOG = log;
  const repo = path.resolve(HERE, '..', '..');
  const bin = path.join(repo, 'tests', 'hooks', 'fixtures', 'mock-classifier.sh');
  const classifier = await import(`../../.claude/hooks/lib/classifier.js?t=${Date.now()}`);
  // Адаптер запуска приходит ПАРАМЕТРОМ: сам классификатор его не выбирает.
  const adapter = await import(`../../.claude/hooks/lib/classify-bash.js?t=${Date.now()}`);
  try {
    process.env.PLAN_CLASSIFIER = 'off';
    classifier.classify(adapter, bin, 'cover', [], 'проба');
    assert.equal(fs.existsSync(log), false, 'выключенный классификатор модель не звал — записи нет');
    delete process.env.PLAN_CLASSIFIER;
    classifier.classify(adapter, bin, 'cover', [], 'проба');
    assert.match(fs.readFileSync(log, 'utf8'), /"kind":"model"/, 'обычный вызов пишется');
    // Без адаптера возможности нет вовсе, и это НАЗВАНО, а не сведено к «модель
    // промолчала»: молчание тут неотличимо от настоящего неответа модели.
    assert.equal(classifier.classify(null, bin, 'cover', [], 'проба'), 'UNSUPPORTED');
  } finally {
    delete process.env.PLAN_CLASSIFIER;
    delete process.env.CRAFT_METRICS_LOG;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// --- узнавание вызова и пуша -----------------------------------------------------

test('хеш вызова считает ВЕСЬ данный ему вход и не зависит от порядка полей', () => {
  const a = hash.callHash('Bash', { command: 'git push' });
  assert.notEqual(a, hash.callHash('Bash', { command: 'git status' }));
  assert.equal(
    hash.callHash('Edit', { file_path: 'a', old_string: 'x', new_string: 'y' }),
    hash.callHash('Edit', { new_string: 'y', old_string: 'x', file_path: 'a' }),
    'порядок полей во входе не обещан — хеш от него не зависит',
  );
  // Отсев служебных полей — дело адаптера харнеса, а не этой функции: что
  // подали, то и сосчитано (кейс на отсев — в write-targets.test.mjs).
  assert.notEqual(a, hash.callHash('Bash', { command: 'git push', description: 'Push branch' }));
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
  const next = summarize([...base,
    { kind: 'prompt', ts: T(3), turn: 2 },
    { kind: 'pre', ts: T(4), turn: 2, tool: 'Edit', id: 'b', decision: 'allow', h: 'h1' },
  ]);
  assert.equal(next.false_denies, 1, 'ход сразу после реплики — это и есть ложный отказ');

  const later = summarize([...base,
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

test('сводка: серия ошибок не переходит через реплику Влада', () => {
  const records = [
    { kind: 'prompt', ts: T(1), turn: 1 },
    { kind: 'post', ts: T(2), turn: 1, tool: 'Bash', id: 'a', error: true },
    { kind: 'post', ts: T(3), turn: 1, tool: 'Bash', id: 'b', error: true },
    { kind: 'prompt', ts: T(4), turn: 2 },
    { kind: 'post', ts: T(5), turn: 2, tool: 'Bash', id: 'c', error: true },
  ];
  assert.equal(summarize(records).signals.error_streak_max, 2,
    'три ошибки, но между второй и третьей — вмешательство Влада');
});

// Хеш вызова канонизируется на ВСЕХ уровнях: порядок полей во вложенном объекте
// смысла не несёт, а на разном хеше повтор после отказа не узнавался и ложный
// отказ не засчитывался. Порядок элементов массива, наоборот, значим.
test('хеш вызова: вложенные поля сортируются, порядок массива значим', () => {
  const a = { file_path: '/a', edits: [{ old_string: 'x', new_string: 'y' }] };
  const b = { edits: [{ new_string: 'y', old_string: 'x' }], file_path: '/a' };
  assert.equal(hash.callHash('MultiEdit', a), hash.callHash('MultiEdit', b));
  assert.notEqual(
    hash.callHash('MultiEdit', { edits: [{ t: 1 }, { t: 2 }] }),
    hash.callHash('MultiEdit', { edits: [{ t: 2 }, { t: 1 }] }),
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
  fs.rmSync(dir, { recursive: true, force: true });
});

// Хук стоит в цепочке хода: ждать чужой лок дольше отведённого срока он не
// вправе. Не дождался — состояние не трогается, а пропуск виден строкой в
// журнале: молчаливый пропуск выглядит как ход, которого не было.
test('состояние под занятым локом: запись пропускается и названа в журнале', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-skip-'));
  const log = path.join(dir, 'm.jsonl');
  const state = `${log}.state.json`;
  fs.mkdirSync(`${state}.lock`);
  fs.writeFileSync(path.join(`${state}.lock`, 'owner'), String(process.pid));

  const started = Date.now();
  let ran = false;
  const out = metrics.updateState(log, () => { ran = true; return 'значение'; });
  const spent = Date.now() - started;

  assert.equal(ran, false, 'правка состояния не выполнялась');
  assert.equal(out, undefined);
  assert.ok(spent < 3000, `хук не завис на чужом локе: ${spent} мс`);
  const line = fs.readFileSync(log, 'utf8').trim().split('\n').pop();
  assert.match(line, /"kind":"skip"/, 'пропуск назван в журнале');
  assert.match(line, /"what":"state"/);
  fs.rmSync(dir, { recursive: true, force: true });
});

// Переуказание — это обращение К АГЕНТУ, а не разговор о мире. Одиночные
// усилители («опять», «снова», «ещё раз») из словаря убраны: «запусти тесты ещё
// раз» и «в который раз упал CI» — обычная работа, а признак на них срабатывал
// и завышал число переуказаний в каждой сводке.
test('переуказание считается по обращению к агенту, а не по усилителю', () => {
  for (const p of ['я же просил не трогать README', 'я уже просил не трогать README',
    'я же просила не трогать README', 'я уже говорила про хвосты',
    'повторяю: не трогай прод',
    'сколько раз можно говорить', 'русским языком: без хвостов']) {
    assert.equal(metrics.looksLikeReinstruction(p), true, p);
  }
  for (const p of ['запусти тесты ещё раз', 'в который раз упал CI', 'опять красный CI',
    'снова падает установка', 'поправь README', 'сноваяркий']) {
    assert.equal(metrics.looksLikeReinstruction(p), false, p);
  }
});

// Вежливость вычёркивается, а не гасит реплику целиком: «спасибо, но я же
// просил» — это поправка с вежливым зачином. Чисто вежливая реплика остатка не
// оставляет.
test('вежливый зачин не отменяет переуказания, а сам переуказанием не становится', () => {
  assert.equal(metrics.looksLikeReinstruction('Спасибо, но я же просил не трогать README'), true);
  assert.equal(metrics.looksLikeReinstruction('благодарю, повторяю: тесты гоняем до пуша'), true);
  assert.equal(metrics.looksLikeReinstruction('Ещё раз спасибо!'), false);
  assert.equal(metrics.looksLikeReinstruction('и снова здравствуйте'), false);
  assert.equal(metrics.looksLikeReinstruction('спасибо!'), false);
});

// Нулевой ход — это Stop служебного вызова ДО первой реплики: хода ещё не было.
// Считая его, сводка давала три хода при двух и «ход без прогресса», которого
// не случалось, а токены первого хода брались нулевые.
test('нулевой ход не считается ни ходом, ни ходом без прогресса, ни первым', () => {
  const records = [
    { kind: 'session', ts: T(0), turn: 0, sid: 's' },
    { kind: 'stop', ts: T(1), turn: 0, blocked_by: '', usage: { input: 9, output: 9 }, no_progress: true },
    { kind: 'prompt', ts: T(2), turn: 1 },
    { kind: 'stop', ts: T(3), turn: 1, blocked_by: '', usage: { input: 1, output: 2 }, no_progress: false },
    { kind: 'prompt', ts: T(4), turn: 2 },
    { kind: 'stop', ts: T(5), turn: 2, blocked_by: '', usage: { input: 4, output: 8 }, no_progress: true },
  ];
  const s = summarize(records);
  assert.equal(s.turns, 2, 'служебный Stop до первой реплики ходом не стал');
  assert.equal(s.signals.turns_without_progress, 1, 'нулевой ход в ходы без прогресса не идёт');
  assert.deepEqual(s.tokens_first_turn, { input: 1, output: 2, cache_read: 0, cache_create: 0 },
    'первый ход — первый НАСТОЯЩИЙ ход, а не служебный Stop до реплики');
  assert.equal(s.tokens.input, 14, 'в общие токены служебный Stop входит: он тоже стоил денег');

  // Журнал живёт в /tmp и переживает не всё: контейнер перезапустился, сессия
  // продолжилась — и записи начинаются с седьмого хода. Ходы считаются по числу
  // РАЗЛИЧНЫХ номеров: по максимуму номера сводка сказала бы «восемь ходов»
  // там, где их было два.
  const resumed = [
    { kind: 'prompt', ts: T(6), turn: 7 },
    { kind: 'stop', ts: T(7), turn: 7, blocked_by: '', usage: {}, no_progress: false },
    { kind: 'prompt', ts: T(8), turn: 8 },
    { kind: 'stop', ts: T(9), turn: 8, blocked_by: '', usage: {}, no_progress: false },
  ];
  assert.equal(summarize(resumed).turns, 2, 'ходы — число различных номеров, а не максимум');
});

// --- сшивка события с его решением ---------------------------------------------
//
// Наблюдатель зовётся ПЕРВЫМ в цепочке (иначе отказ обрывал бы её до него), поэтому
// исхода в записи события нет: решение приходит отдельной строкой от того, кто
// решил, и переносится в журнал метрик следующим событием. Кейсы держат то, ради
// чего это затевалось: четыре метрики, которые считаются по исходу, считаются
// по-прежнему.
const line = (n) => `2026-01-01T00:0${n}:00.000Z`;

test('сшивка: отказ приходит СТРОКОЙ и считается отказом', () => {
  const records = [
    { kind: 'session', ts: line(0), turn: 0, key: 'o0', harness: 'claude', sid: 's' },
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'pre', ts: line(2), turn: 1, key: 'o2', tool: 'Bash', id: 'c1', h: 'H1' },
    {
      kind: 'decision', ts: line(2), key: 'o2', sid: 's', call_id: 'c1', event: 'pre-tool',
      hook: 'universal-guard-plan-gate', outcome: 'deny', class: 'gate.empty', h: 'H1', tool: 'Bash',
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.denies.total, 1);
  assert.deepEqual(s.denies.by_class, { 'gate.empty': 1 });
});

test('сшивка: строки решения нет — это проход, а не пропуск', () => {
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'pre', ts: line(2), turn: 1, key: 'o2', tool: 'Bash', id: 'c1', h: 'H1', plan: true },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.denies.total, 0);
  assert.equal(s.plan.shown, 1, 'показ плана без отказа — показ');
});

test('сшивка: ПОВТОРНО перенесённая строка счёт не двоит', () => {
  const decision = {
    kind: 'decision', ts: line(2), key: 'o2', sid: 's', call_id: 'c1', event: 'pre-tool',
    hook: 'universal-guard-plan-gate', outcome: 'deny', class: 'gate.empty', h: 'H1', tool: 'Bash',
  };
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'pre', ts: line(2), turn: 1, key: 'o2', tool: 'Bash', id: 'c1', h: 'H1' },
    decision, { ...decision },
  ];
  // Журнал решений могли перечитать сначала (его подмели, сессия вернулась) —
  // строка легла дважды. Считается ЗАПИСЬ СОБЫТИЯ, а их по одной на событие.
  assert.equal(summarize(records, { sid: 's' }).denies.total, 1);
});

test('сшивка: решение принадлежит СВОЕМУ появлению события, а не следующему', () => {
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'stop', ts: line(2), turn: 1, key: 'o2' },
    {
      kind: 'decision', ts: line(2), key: 'o2', sid: 's', call_id: '', event: 'stop',
      hook: 'universal-stop-quality-gate', outcome: 'block', class: '',
    },
    { kind: 'stop', ts: line(3), turn: 1, key: 'o3' },
  ];
  const s = summarize(records, { sid: 's' });
  assert.deepEqual(s.stop_blocks, { 'universal-stop-quality-gate': 1 },
    'второй конец хода блокировку первого не наследует');
});

test('сшивка: признак инцидента приходит той же строкой канала', () => {
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'flag', ts: line(1), key: 'o1', sid: 's', call_id: '', event: 'prompt', flag: 'incident' },
    { kind: 'prompt', ts: line(2), turn: 2, key: 'o2' },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.incidents.detected, 1, 'признак принадлежит своей реплике, а не всем следующим');
});

test('сшивка: ложный отказ виден и когда исход пришёл строкой', () => {
  const records = [
    { kind: 'prompt', ts: line(0), turn: 1, key: 'o0' },
    { kind: 'pre', ts: line(1), turn: 1, key: 'o1', tool: 'Bash', id: 'c1', h: 'H1' },
    {
      kind: 'decision', ts: line(1), key: 'o1', sid: 's', call_id: 'c1', event: 'pre-tool',
      hook: 'universal-guard-plan-gate', outcome: 'deny', class: 'gate.uncovered', h: 'H1', tool: 'Bash',
    },
    // Влад вмешался репликой — и ТОТ ЖЕ вызов прошёл.
    { kind: 'prompt', ts: line(2), turn: 2, key: 'o2' },
    { kind: 'pre', ts: line(3), turn: 2, key: 'o3', tool: 'Bash', id: 'c2', h: 'H1' },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.false_denies, 1);
  assert.equal(s.denies.total, 1);
});

test('сшивка: решение ПОСЛЕ вызова не ложится на запись ДО него', () => {
  // Идентификатор вызова у обоих событий один — он и есть ключ. Сшивай по голому
  // ключу, и строка, записанная на событии после вызова, стала бы исходом самого
  // вызова: тут отказ появился бы у прошедшего вызова из ниоткуда.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'pre', ts: line(2), turn: 1, key: 'c1', tool: 'Bash', id: 'c1', h: 'H1' },
    { kind: 'post', ts: line(3), turn: 1, key: 'c1', tool: 'Bash', id: 'c1' },
    {
      kind: 'decision', ts: line(3), key: 'c1', sid: 's', call_id: 'c1', event: 'post-tool',
      hook: 'universal-что-нибудь-после-вызова', outcome: 'deny', class: 'после-вызова',
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.denies.total, 0, 'вызов ПРОШЁЛ: отказа на событии до него не было');
  assert.deepEqual(s.denies.by_class, {});
});

test('сшивка: замеры ПОСЛЕ вызова не доказывают доставку строк ДО него', () => {
  // Обратная сторона той же пары. Замеры события после вызова несут тот же ключ;
  // считай их доказательством — и потерянный отказ события до вызова прочитался
  // бы как проход, ради чего весь этот счёт и заведён.
  //
  // Спрашивается ИМЕННО про запись до вызова, а не суммарное число неизвестных:
  // по голому ключу неизвестной становится запись ПОСЛЕ вызова, и сумма остаётся
  // той же единицей — кейс зеленел бы на сломанной паре.
  const records = [
    { kind: 'pre', ts: line(1), turn: 1, key: 'c1', disp: true, tool: 'Bash', id: 'c1', h: 'H1' },
    { kind: 'post', ts: line(2), turn: 1, key: 'c1', disp: true, tool: 'Bash', id: 'c1' },
    {
      kind: 'timing', ts: line(2), key: 'c1', sid: 's', call_id: 'c1', event: 'post-tool', hooks: {},
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.unknown_events, 1);
  // Исход вызова неизвестен — значит он не посчитан ни проходом, ни отказом.
  assert.equal(s.denies.total, 0);
  assert.equal(s.false_denies, 0);
  // А вот ЗАПИСЬ ПОСЛЕ вызова свои строки дождалась: ошибок инструмента нет.
  assert.equal(s.tool_errors, 0);
});

test('сшивка: неизвестен именно вызов, а не его завершение', () => {
  // То же событие, но показанным планом: `plan.shown` считается ровно по записи
  // ДО вызова, поэтому по нему видно, КАКАЯ из двух записей осталась без строк.
  const records = [
    {
      kind: 'pre', ts: line(1), turn: 1, key: 'c1', disp: true, tool: 'ExitPlanMode', id: 'c1', h: 'H1', plan: true,
    },
    { kind: 'post', ts: line(2), turn: 1, key: 'c1', disp: true, tool: 'ExitPlanMode', id: 'c1' },
    {
      kind: 'timing', ts: line(2), key: 'c1', sid: 's', call_id: 'c1', event: 'post-tool', hooks: {},
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.plan.shown, 0, 'показ не засчитан: исход вызова неизвестен');
  assert.equal(s.plan.bounced, 0);
});

test('сшивка: одна блокировка не достаётся двум одинаковым концам хода', () => {
  // У событий без идентификатора вызова ключ — хеш их байтов, и у двух
  // байт-в-байт одинаковых концов хода он ОДИН. Раздавай решение всем записям
  // пары — одна блокировка посчиталась бы дважды.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'stop', ts: line(2), turn: 1, key: 'k' },
    {
      kind: 'decision', ts: line(2), key: 'k', sid: 's', call_id: '', event: 'stop',
      hook: 'universal-instinct-flush', outcome: 'block', class: '',
    },
    { kind: 'stop', ts: line(3), turn: 1, key: 'k' },
  ];
  const s = summarize(records, { sid: 's' });
  assert.deepEqual(s.stop_blocks, { 'universal-instinct-flush': 1 });
});

test('сшивка: две блокировки двух одинаковых концов хода считаются обе', () => {
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'stop', ts: line(2), turn: 1, key: 'k' },
    { kind: 'stop', ts: line(3), turn: 1, key: 'k' },
    {
      kind: 'decision', ts: line(2), key: 'k', sid: 's', call_id: '', event: 'stop',
      hook: 'universal-instinct-flush', outcome: 'block', class: '',
    },
    {
      kind: 'decision', ts: line(3), key: 'k', sid: 's', call_id: '', event: 'stop',
      hook: 'universal-instinct-flush', outcome: 'block', class: '',
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.deepEqual(s.stop_blocks, { 'universal-instinct-flush': 2 });
});

test('сшивка: журнал ПРЕЖНЕГО слоя не теряет отказ после обновления', () => {
  // Записи и строки, написанные до смены ключа, несут номер появления процесса
  // (`occ` у записи, `occurrence` у строки). Сводка пересобирается по ВСЕМУ
  // журналу сессии на каждом конце хода, а слой обновляют из живой сессии:
  // читай тут только новое поле — отказ пропал бы совсем, даже не оставшись
  // неизвестным.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, occ: 'старый-1', disp: true },
    { kind: 'pre', ts: line(2), turn: 1, occ: 'старый-2', disp: true, tool: 'Bash', id: 'c1', h: 'H1' },
    {
      kind: 'decision', ts: line(2), occurrence: 'старый-2', sid: 's', call_id: 'c1', event: 'pre-tool',
      hook: 'universal-session-anchor', outcome: 'deny', class: 'session-anchor',
    },
    // Тот же ход уже на новом слое: ключ считается по событию.
    { kind: 'pre', ts: line(3), turn: 1, key: 'c2', disp: true, tool: 'Bash', id: 'c2', h: 'H2' },
    {
      kind: 'decision', ts: line(3), key: 'c2', sid: 's', call_id: 'c2', event: 'pre-tool',
      hook: 'universal-fact-gate', outcome: 'deny', class: 'fact-gate',
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.denies.total, 2, 'отказы обеих эпох считаются');
  assert.deepEqual(s.denies.by_class, { 'session-anchor': 1, 'fact-gate': 1 });
});

test('сшивка: своё поле записи сильнее строки — журнал переживает обновление слоя', () => {
  // Записи, сделанные прежним слоем, несут исход прямо в себе; строки решения к
  // ним нет вовсе, и «нет строки» для них не значит «прошёл».
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    {
      kind: 'pre', ts: line(2), turn: 1, tool: 'Bash', id: 'c1', h: 'H1', decision: 'deny', class: 'gate.empty', by: 'x',
    },
  ];
  assert.equal(summarize(records, { sid: 's' }).denies.total, 1);
});

test('сшивка: строки события не доехали — исход НЕИЗВЕСТЕН, а не «прошёл»', () => {
  // Под диспетчером у события обязаны появиться строки канала: замеры он кладёт на
  // каждом. Ни одной строки с этой парой — значит канал не доехал
  // (сорвался, подмели, сессия кончилась на этом событии). Считать такую пустоту
  // проходом значило бы поменять ЗНАК: отбитый показ плана уехал бы показанным.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1', disp: true },
    {
      kind: 'timing', ts: line(1), key: 'o1', sid: 's', call_id: '', event: 'prompt', hooks: {},
    },
    {
      kind: 'pre', ts: line(2), turn: 1, key: 'o2', disp: true, tool: 'ExitPlanMode', id: 'c1', h: 'H1', plan: true,
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.plan.shown, 0, 'неизвестный исход показом не считается');
  assert.equal(s.plan.bounced, 0, 'и отказом тоже: мы не знаем');
  assert.equal(s.denies.total, 0);
  assert.equal(s.unknown_events, 1, 'зато видно, сколько событий осталось без исхода');
});

test('сшивка: строки доехали, решения нет — это проход', () => {
  // Ровно та же запись, но замеры её события переехали: канал отработал, и
  // молчание гварда значит именно проход.
  const records = [
    {
      kind: 'pre', ts: line(2), turn: 1, key: 'o2', disp: true, tool: 'ExitPlanMode', id: 'c1', h: 'H1', plan: true,
    },
    {
      kind: 'timing', ts: line(2), key: 'o2', sid: 's', call_id: 'c1', event: 'pre-tool', hooks: {},
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.plan.shown, 1);
  assert.equal(s.unknown_events, 0);
});

test('сшивка: потерянный признак реплики тоже виден числом, а не тихим false', () => {
  // Инцидент приходит тем же каналом. `incident: false` на потерянной строке
  // ронял бы долю разборов так же тихо, как пустота роняла отказ.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1', disp: true },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.incidents.detected, 0);
  assert.equal(s.unknown_events, 1);
});

test('сшивка: доехавшая строка решения важнее того, что канал сорвался ПОЗЖЕ', () => {
  // Строки этого события приехали, а на следующем канал упал. Первое событие от
  // этого неизвестным не становится: доказательство доставки у него своё.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1', disp: true },
    {
      kind: 'timing', ts: line(1), key: 'o1', sid: 's', call_id: '', event: 'prompt', hooks: {},
    },
    { kind: 'pre', ts: line(2), turn: 1, key: 'o2', disp: true, tool: 'Bash', id: 'c1', h: 'H1' },
    {
      kind: 'decision', ts: line(2), key: 'o2', sid: 's', call_id: 'c1', event: 'pre-tool',
      hook: 'universal-fact-gate', outcome: 'deny', class: 'fact-gate',
    },
    // Следующее событие своих строк не дождалось — оно и только оно неизвестно.
    { kind: 'stop', ts: line(3), turn: 1, key: 'o3', disp: true },
    { kind: 'skip', ts: line(3), what: 'decisions', capability: 'decision-log' },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.denies.total, 1, 'отказ, чья строка доехала, посчитан');
  assert.deepEqual(s.stop_blocks, {}, 'а про конец хода мы ничего не знаем');
  assert.equal(s.unknown_events, 1);
});

test('сшивка: доказательство доставки — ЗАМЕРЫ, а не любая строка канала', () => {
  // Замеры диспетчер кладёт на каждом событии; признак и решение — нет. Считай
  // доказательством любую строку, и событие, у которого доехал только признак,
  // прочиталось бы как проход, хотя строка решения по нему потерялась.
  const records = [
    {
      kind: 'pre', ts: line(1), turn: 1, key: 'o1', disp: true, tool: 'Bash', id: 'c1', h: 'H1',
    },
    {
      kind: 'flag', ts: line(1), key: 'o1', sid: 's', call_id: 'c1', event: 'pre-tool', flag: 'incident',
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.denies.total, 0);
  assert.equal(s.unknown_events, 1, 'замеров нет — исход неизвестен, а не «проход»');
});

test('сшивка: дописанный контекст НЕ затирает отказ, в каком бы порядке ни легли строки', () => {
  const base = {
    kind: 'pre', ts: line(1), turn: 1, key: 'o1', disp: true, tool: 'Bash', id: 'c1', h: 'H1',
  };
  const timing = {
    kind: 'timing', ts: line(1), key: 'o1', sid: 's', call_id: 'c1', event: 'pre-tool', hooks: {},
  };
  const none = {
    kind: 'decision', ts: line(1), key: 'o1', sid: 's', call_id: 'c1', event: 'pre-tool',
    hook: 'universal-что-нибудь-дописал', outcome: 'none', class: '',
  };
  const deny = {
    kind: 'decision', ts: line(1), key: 'o1', sid: 's', call_id: 'c1', event: 'pre-tool',
    hook: 'universal-fact-gate', outcome: 'deny', class: 'fact-gate',
  };
  for (const order of [[none, deny], [deny, none]]) {
    const s = summarize([base, timing, ...order], { sid: 's' });
    assert.equal(s.denies.total, 1, `отказ обязан пережить порядок ${order.map((r) => r.outcome).join('→')}`);
    assert.equal(s.denies.by_class['fact-gate'], 1);
  }
});

test('сшивка: дописанный контекст — не решение, вызов считается прошедшим', () => {
  // Инжектор пишет исход `none`. Считать его отказом нельзя, но и «не allow» тоже:
  // тогда первый же инжектор на событии до вызова вычел бы вызов из ложных отказов
  // и из показов плана — они сравнивают ровно с `allow`.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1', disp: true },
    {
      kind: 'timing', ts: line(1), key: 'o1', sid: 's', call_id: '', event: 'prompt', hooks: {},
    },
    {
      kind: 'pre', ts: line(2), turn: 1, key: 'o2', disp: true, tool: 'ExitPlanMode', id: 'c1', h: 'H1', plan: true,
    },
    {
      kind: 'decision', ts: line(2), key: 'o2', sid: 's', call_id: 'c1', event: 'pre-tool',
      hook: 'universal-inject-что-нибудь', outcome: 'none', class: '',
    },
    // Замеры цепочки диспетчер кладёт на КАЖДОМ событии — без них форма записей
    // была бы не той, что бывает на живом пути, и кейс проверял бы небылицу.
    {
      kind: 'timing', ts: line(2), key: 'o2', sid: 's', call_id: 'c1', event: 'pre-tool', hooks: {},
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.plan.shown, 1, 'план показан: инжектор его не отбивал');
  assert.equal(s.denies.total, 0);
  assert.equal(s.unknown_events, 0);
});

test('сшивка: дописанный контекст НЕ доказывает доставку — без замеров исход неизвестен', () => {
  // Строка `none` говорит «я не решал». Считай её ответом на вопрос «а доехали ли
  // строки этого события», и потерянный на том же появлении отказ прочитался бы
  // как проход: показ плана превратился бы в состоявшийся, а отказ — в тишину.
  const records = [
    {
      kind: 'pre', ts: line(1), turn: 1, key: 'o1', disp: true, tool: 'ExitPlanMode', id: 'c1', h: 'H1', plan: true,
    },
    {
      kind: 'decision', ts: line(1), key: 'o1', sid: 's', call_id: 'c1', event: 'pre-tool',
      hook: 'universal-inject-что-нибудь', outcome: 'none', class: '',
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.unknown_events, 1, 'замеров нет — канал по этому событию не доказан');
  assert.equal(s.plan.shown, 0, 'показ плана не засчитывается по неизвестному исходу');
});

test('сшивка: найденная строка сильнее отметки о пропаже', () => {
  // Канал сорвался позже, а решение этого события уже доехало — оно и есть факт.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1' },
    { kind: 'pre', ts: line(2), turn: 1, key: 'o2', disp: true, tool: 'Bash', id: 'c1', h: 'H1' },
    {
      kind: 'decision', ts: line(2), key: 'o2', sid: 's', call_id: 'c1', event: 'pre-tool',
      hook: 'universal-fact-gate', outcome: 'deny', class: 'fact-gate',
    },
    { kind: 'skip', ts: line(3), what: 'decisions', capability: 'decision-log', key: 'o2' },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.denies.total, 1);
  assert.equal(s.unknown_events, 0);
});

// Признак потери строки канала. Его спрашивают ЗАМЕРЫ: они служат свёртке
// доказательством, что канал по событию отработал, и класть их после того, как
// решение исчезло совсем, значило бы выдать отказ за проход.
//
// Признак — на процесс и не сбрасывается, поэтому кейс сравнивает его С ПРЕЖНИМ
// значением, а не с нулём: от места в файле он не зависит.
test('сшивка: ДОЕХАВШИЙ признак инцидента не выбрасывается вместе с «не знаю»', () => {
  // Реплика осталась без полного канала — исход её неизвестен, и это честно.
  // Но признак инцидента по ней УЖЕ доехал, и терять его вместе с пометкой значит
  // ронять долю разборов ровно так же тихо, как терялся бы сам признак.
  const records = [
    { kind: 'prompt', ts: line(1), turn: 1, key: 'o1', disp: true },
    {
      kind: 'flag', ts: line(1), key: 'o1', sid: 's', call_id: '', event: 'prompt', flag: 'incident',
    },
  ];
  const s = summarize(records, { sid: 's' });
  assert.equal(s.unknown_events, 1, 'замеров нет — исход реплики неизвестен');
  assert.equal(s.incidents.detected, 1, 'но инцидент замечен, и это уже известно');
});

test('канал: потерянная строка снимает ПРИЗНАК доставки', () => {
  const prev = process.env.CRAFT_METRICS_LOG;
  const prevSid = process.env.CRAFT_SESSION_ID;
  try {
    delete process.env.CRAFT_METRICS_LOG;
    delete process.env.CRAFT_SESSION_ID;
    // Признак — на процесс и не сбрасывается, поэтому кейс не спрашивает «сейчас
    // ноль», а СРАВНИВАЕТ с тем, что было: иначе он ломался бы у любого, кто
    // допишет тест после него, и чинить пришлось бы порядок строк в файле.
    const before = metrics.channelLost();
    // Строка легла в журнал решений — терять нечего.
    assert.equal(metrics.keepChannelLine({ ok: true, line: { kind: 'decision' } }), true);
    assert.equal(metrics.channelLost(), before, 'легшая строка признака не поднимает');
    // Не легла, и запасного журнала тоже нет: строка исчезла совсем.
    assert.equal(metrics.keepChannelLine({ ok: false, line: { kind: 'decision' } }), false);
    assert.equal(metrics.channelLost(), true, 'потеря обязана быть видна тому, кто кладёт доказательство');
  } finally {
    if (prev === undefined) delete process.env.CRAFT_METRICS_LOG; else process.env.CRAFT_METRICS_LOG = prev;
    if (prevSid === undefined) delete process.env.CRAFT_SESSION_ID; else process.env.CRAFT_SESSION_ID = prevSid;
  }
});
