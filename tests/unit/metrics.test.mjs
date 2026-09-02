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
