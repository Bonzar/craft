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

const SID = 'test-session-id';

async function freshPaths(env = {}) {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith('CRAFT_') || key.startsWith('HOOK_ONCE') || key.endsWith('_MARKER')
        || key.endsWith('_STATE') || key === 'OBSERVE_BUFFER' || key === 'FACT_GATE_STATE_DIR') {
      delete process.env[key];
    }
  }
  process.env.CLAUDE_CODE_SESSION_ID = SID;
  Object.assign(process.env, env);
  // Кэша у модуля нет, но импорт с меткой делает намерение явным: каждый тест
  // читает окружение заново.
  return import(`../../.claude/hooks/lib/paths.js?t=${Date.now()}${Math.random()}`);
}

// Эталон: имя переменной-переопределения → путь по умолчанию при заданной
// сессии. Меняешь строку здесь — меняешь контракт со всеми хуками разом.
const EXPECTED = {
  CRAFT_APPROVAL_REGISTRY: `/tmp/craft-approvals.${SID}.jsonl`,
  CRAFT_PLAN_FILE_MARKER: `/tmp/plan-file.${SID}.path`,
  CRAFT_PLAN_CRITIC_MARKER: `/tmp/plan-critic.${SID}.done`,
  CRAFT_PLAN_CRITIC_PENDING: `/tmp/plan-critic.${SID}.pending`,
  CRAFT_PLAN_CRITIC_RUNS: `/tmp/plan-critic.${SID}.runs`,
  CRAFT_PLAN_CRITIC_ROUND: `/tmp/plan-critic.${SID}.round`,
  CRAFT_PLAN_SHOWN_MARKER: `/tmp/plan-shown.${SID}`,
  CRAFT_SERVICE_TURN_MARKER: `/tmp/plan-service-turn.${SID}`,
  OBSERVE_BUFFER: `/tmp/agent-observe.${SID}.log`,
  INCIDENT_CLOSURE_MARKER: `/tmp/incident-closure.${SID}.armed`,
  ROUTINE_FACTS_MARKER: `/tmp/routine-facts.${SID}.reminded`,
  SYNC_SYSTEM_STATE: `/tmp/sync-system.${SID}`,
  RELATIVE_LINK_STATE: `/tmp/relative-link.${SID}.blocked`,
};

test('пути состояния совпадают с закреплённым эталоном', async () => {
  const paths = await freshPaths();
  const byEnv = {
    CRAFT_APPROVAL_REGISTRY: paths.approvalRegistry(),
    CRAFT_PLAN_FILE_MARKER: paths.planFileMarker(),
    CRAFT_PLAN_CRITIC_MARKER: paths.planCriticMarker(),
    CRAFT_PLAN_CRITIC_PENDING: paths.planCriticPending(),
    CRAFT_PLAN_CRITIC_RUNS: paths.planCriticRuns(),
    CRAFT_PLAN_CRITIC_ROUND: paths.planCriticRound(),
    CRAFT_PLAN_SHOWN_MARKER: paths.planShownMarker(),
    CRAFT_SERVICE_TURN_MARKER: paths.serviceTurnMarker(),
    OBSERVE_BUFFER: paths.observeBuffer(),
    INCIDENT_CLOSURE_MARKER: paths.incidentClosureMarker(),
    ROUTINE_FACTS_MARKER: paths.routineFactsMarker(),
    SYNC_SYSTEM_STATE: paths.syncSystemState(),
    RELATIVE_LINK_STATE: paths.relativeLinkState(),
  };

  // Набор сверяется целиком: новый путь без строки в эталоне так же опасен, как
  // переименованный, — его никто не проверяет.
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
  const paths = await freshPaths({ CLAUDE_CODE_SESSION_ID: '' });
  assert.equal(paths.approvalRegistry(), '', 'реестр держит одобрения: общий default открыл бы записи чужой сессии');
  // А счётчики и метки общий default переживают: они ничего не открывают.
  assert.equal(paths.planCriticRuns(), '/tmp/plan-critic.default.runs');
});
