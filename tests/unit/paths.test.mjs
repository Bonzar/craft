// Имена файлов состояния — контракт между хуками: по ним они разговаривают друг
// с другом, и разъехавшаяся строчка рвёт связку молча. Пока слой переезжал,
// эталоном служили дефолты bash-версий; теперь их нет, и эталон закреплён здесь
// списком — тест намеренно ловит ЛЮБОЕ переименование, а не подтверждает код
// сам собой.
//
// Расширение .mjs, а не .js: в каталоге тестов нет манифеста модулей, и .js
// читался бы как обычный скрипт, которому импорт недоступен.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';

const SID = 'test-session-id';
// Каталог состояния ОДИН: `stateDir()`. Прежде их было два — зашитый `/tmp` у
// одних путей и `os.tmpdir()` у других, — и на подменённом TMPDIR половина
// состояния уезжала в чужой каталог. Эталон зашит не в `/tmp`, а в ту же
// формулу: иначе он краснел бы на подменённом TMPDIR там, где код прав.
const STATE = os.tmpdir();
const TMP = STATE;

async function freshPaths(env = {}) {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith('CRAFT_') || key.startsWith('HOOK_ONCE') || key.endsWith('_MARKER')
        || key.endsWith('_STATE') || key === 'OBSERVE_BUFFER' || key === 'FACT_GATE_STATE_DIR') {
      delete process.env[key];
    }
  }
  // Сессия — под СВОИМ именем: имя переменной харнеса знает только адаптер
  // события, а paths.js читает уже переведённое значение. Ставится ПОСЛЕ уборки:
  // та сносит всё с префиксом CRAFT_.
  process.env.CRAFT_SESSION_ID = SID;
  Object.assign(process.env, env);
  // Кэша у модуля нет, но импорт с меткой делает намерение явным: каждый тест
  // читает окружение заново.
  return import(`../../.claude/hooks/lib/paths.js?t=${Date.now()}${Math.random()}`);
}

// Эталон: имя переменной-переопределения → путь по умолчанию при заданной
// сессии. Меняешь строку здесь — меняешь контракт со всеми хуками разом.
const EXPECTED = {
  CRAFT_APPROVAL_REGISTRY: `${STATE}/craft-approvals.${SID}.jsonl`,
  CRAFT_PLAN_FILE_MARKER: `${STATE}/plan-file.${SID}.path`,
  CRAFT_PLAN_CRITIC_MARKER: `${STATE}/plan-critic.${SID}.done`,
  CRAFT_PLAN_CRITIC_PENDING: `${STATE}/plan-critic.${SID}.pending`,
  CRAFT_PLAN_CRITIC_RUNS: `${STATE}/plan-critic.${SID}.runs`,
  CRAFT_PLAN_CRITIC_ROUND: `${STATE}/plan-critic.${SID}.round`,
  CRAFT_PLAN_SHOWN_MARKER: `${STATE}/plan-shown.${SID}`,
  CRAFT_SERVICE_TURN_MARKER: `${STATE}/plan-service-turn.${SID}`,
  OBSERVE_BUFFER: `${STATE}/agent-observe.${SID}.log`,
  INCIDENT_CLOSURE_MARKER: `${STATE}/incident-closure.${SID}.armed`,
  ROUTINE_FACTS_MARKER: `${STATE}/routine-facts.${SID}.reminded`,
  SYNC_SYSTEM_STATE: `${TMP}/sync-system.${SID}`,
  RELATIVE_LINK_STATE: `${TMP}/relative-link.${SID}.blocked`,
  CRAFT_METRICS_LOG: `${TMP}/metrics.${SID}.jsonl`,
  SESSION_ANCHOR_STATE: `${STATE}/session-anchor.${SID}`,
  CRAFT_DECISION_LOG: `${STATE}/decisions.${SID}.jsonl`,
};

// Имя переменной-переопределения → имя экспорта, который этот путь отдаёт.
const BY_EXPORT = {
  CRAFT_APPROVAL_REGISTRY: 'approvalRegistry',
  CRAFT_PLAN_FILE_MARKER: 'planFileMarker',
  CRAFT_PLAN_CRITIC_MARKER: 'planCriticMarker',
  CRAFT_PLAN_CRITIC_PENDING: 'planCriticPending',
  CRAFT_PLAN_CRITIC_RUNS: 'planCriticRuns',
  CRAFT_PLAN_CRITIC_ROUND: 'planCriticRound',
  CRAFT_PLAN_SHOWN_MARKER: 'planShownMarker',
  CRAFT_SERVICE_TURN_MARKER: 'serviceTurnMarker',
  OBSERVE_BUFFER: 'observeBuffer',
  INCIDENT_CLOSURE_MARKER: 'incidentClosureMarker',
  ROUTINE_FACTS_MARKER: 'routineFactsMarker',
  SYNC_SYSTEM_STATE: 'syncSystemState',
  RELATIVE_LINK_STATE: 'relativeLinkState',
  CRAFT_METRICS_LOG: 'metricsLog',
  SESSION_ANCHOR_STATE: 'sessionAnchor',
  CRAFT_DECISION_LOG: 'decisionLog',
};

// Экспорты модуля, которые путём состояния НЕ являются, — каждый с причиной.
// Список закрывает полноту: всё остальное, что модуль отдаёт наружу, обязано
// стоять в эталоне.
const NOT_A_PATH = new Set([
  'sessionId',      // идентификатор сессии, а не путь
  'repoRootOf',     // корень чекаута по модулю, к состоянию отношения не имеет
  'factGateStateDir', // КАТАЛОГ под состояние факт-гейта, а не файл в нём
  'hookOnceDir',    // каталог меток уступки, имена в нём строит сам hookOnce
  'exemptScopeFile', // путь внутри предодобренной зоны, у него свой кейс
  'stateDir',       // КАТАЛОГ состояния, а не файл в нём
]);

test('пути состояния совпадают с закреплённым эталоном', async () => {
  const paths = await freshPaths();
  const byEnv = Object.fromEntries(
    Object.entries(BY_EXPORT).map(([envName, fn]) => [envName, paths[fn]()]),
  );

  // Набор сверяется целиком: новый путь без строки в эталоне так же опасен, как
  // переименованный, — его никто не проверяет. Полнота держится не на втором
  // списке, написанном рукой (так уже выпал якорь сессии), а на ЭКСПОРТАХ
  // модуля: всё, что он отдаёт наружу, либо стоит в эталоне, либо названо
  // неучастником с причиной.
  const covered = new Set(Object.values(BY_EXPORT));
  const outside = Object.keys(paths).filter((name) => !covered.has(name) && !NOT_A_PATH.has(name));
  assert.deepEqual(outside, [], 'новый экспорт путей: либо в эталон, либо в список неучастников с причиной');
  assert.deepEqual(Object.keys(byEnv).sort(), Object.keys(EXPECTED).sort());
  for (const [name, expected] of Object.entries(EXPECTED)) {
    assert.equal(byEnv[name], expected, `путь ${name} разошёлся с эталоном`);
  }
});

test('переопределение окружением сильнее дефолта', async () => {
  const paths = await freshPaths({ CRAFT_APPROVAL_REGISTRY: '/tmp/свой-реестр' });
  assert.equal(paths.approvalRegistry(), '/tmp/свой-реестр');
});

test('при пустой сессии периметра нет вовсе', async () => {
  const paths = await freshPaths({ CRAFT_SESSION_ID: '' });
  assert.equal(paths.approvalRegistry(), '', 'реестр держит одобрения: общий default открыл бы записи чужой сессии');
  // А счётчики и метки общий default переживают: они ничего не открывают.
  assert.equal(paths.planCriticRuns(), `${STATE}/plan-critic.default.runs`);
});

test('каталог состояния переопределяется одной переменной — и его слушают ВСЕ пути', async () => {
  const paths = await freshPaths({ CRAFT_STATE_DIR: '/tmp/своё-состояние' });
  // Прежде каталогов было два, и подменялся только один: половина состояния
  // уезжала мимо. Кейс держит именно это — обе прежние половины сразу.
  assert.equal(paths.approvalRegistry(), `/tmp/своё-состояние/craft-approvals.${SID}.jsonl`);
  assert.equal(paths.metricsLog(SID), `/tmp/своё-состояние/metrics.${SID}.jsonl`);
  assert.equal(paths.decisionLog(SID), `/tmp/своё-состояние/decisions.${SID}.jsonl`);
  assert.equal(paths.hookOnceDir(), '/tmp/своё-состояние');
});
