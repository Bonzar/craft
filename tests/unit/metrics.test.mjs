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
