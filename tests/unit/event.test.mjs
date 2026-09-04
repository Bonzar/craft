// Каноническое событие: ядро, факты и таблица отображения имён.
//
// Держит границу решения 17: общая часть видит СВОИ имена событий и своё ядро, а
// поля харнеса не покидают адаптера. Кейсы падают ровно там, где границу
// продырявили: неизвестное имя события не должно превращаться в догадку, а
// отсутствующий факт — в молчаливый ноль.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.resolve(HERE, '..', '..', '.claude', 'hooks', 'lib');

const {
  EVENTS, CORE_FIELDS, FACTS, coreEvent, hasFact, missingFact, unsupported,
} = await import(`${LIB}/event.js`);
const { harnessEventName } = await import(`${LIB}/event-claude.js`);

test('ядро даёт ровно поля решения 17 и ничего сверх', () => {
  const core = coreEvent({
    harness: 'claude',
    session_id: 'sid',
    call_id: 'toolu_1',
    event: EVENTS.PRE_TOOL,
    tool: 'Bash',
    input: { command: 'echo hi' },
    cwd: '/repo',
    state_dir: '/tmp',
  });
  assert.deepEqual(Object.keys(core).sort(), [...CORE_FIELDS].sort());
  assert.equal(core.event, 'pre-tool');
  assert.deepEqual(core.input, { command: 'echo hi' });
});

test('неизвестное имя события даёт ПУСТОЕ имя, а не догадку', () => {
  // Догадка запустила бы хук не на том событии; пустое имя просто не совпадёт ни
  // с одной подпиской, и хук промолчит.
  assert.equal(coreEvent({ event: 'PreToolUse' }).event, '', 'имя харнеса именем ядра не является');
  assert.equal(coreEvent({ event: 'что-то новое' }).event, '');
  assert.equal(coreEvent({}).event, '');
});

test('битые значения не роняют ядро и не протекают наружу', () => {
  const core = coreEvent({
    session_id: 42, tool: null, input: ['не объект'], cwd: undefined,
  });
  assert.equal(core.session_id, '');
  assert.equal(core.tool, '');
  assert.deepEqual(core.input, {}, 'массив входом вызова не является');
  assert.equal(core.cwd, '');
});

test('факт есть только когда он ДАН: ноль — факт, отсутствие ключа — нет', () => {
  const zero = { tokens: { input: 0, output: 0 } };
  assert.equal(hasFact(zero, 'tokens'), true, 'нулевые токены — это измеренный ноль');
  assert.equal(hasFact({}, 'tokens'), false);
  assert.equal(hasFact({ journal: '' }, 'journal'), false, 'пустой путь — не путь');
  assert.equal(hasFact({ journal: '/tmp/m.jsonl' }, 'journal'), true);
});

test('нет факта — назван ПЕРВЫЙ недостающий, а не «что-то не так»', () => {
  assert.equal(missingFact({ journal: '/tmp/m.jsonl' }, ['journal']), '');
  assert.equal(missingFact({ journal: '/tmp/m.jsonl' }, ['journal', 'tokens']), 'tokens');
  assert.equal(missingFact({}, [...FACTS]), 'journal');
  assert.equal(missingFact({}, []), '', 'ничего не требуется — ничего и не пропало');
});

test('непокрытое называется возможностью, а не пустотой', () => {
  assert.deepEqual(unsupported('tokens'), { status: 'unsupported', capability: 'tokens' });
  assert.deepEqual(unsupported(), { status: 'unsupported', capability: '' });
});

test('таблица имён переводит в обе стороны и молчит на чужом', () => {
  // Отображение — ЕДИНСТВЕННОЕ место, где имена событий Claude вообще названы.
  assert.equal(harnessEventName(EVENTS.PRE_TOOL), 'PreToolUse');
  assert.equal(harnessEventName(EVENTS.POST_TOOL_FAILURE), 'PostToolUseFailure');
  assert.equal(harnessEventName(EVENTS.STOP), 'Stop');
  assert.equal(harnessEventName('pre-tool'), 'PreToolUse');
  assert.equal(harnessEventName('такого имени нет'), '', 'чужое имя не выдумывается');
  assert.equal(harnessEventName(''), '');
});
