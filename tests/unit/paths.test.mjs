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
// Каталогов у состояния ДВА, и эталон это показывает, а не сглаживает.
// STATE — каталог меток и реестра: `factGateStateDir()` возвращает зашитый
// `/tmp`, и подменённый TMPDIR его не двигает. TMP — то, что берут три
// новых пути (`os.tmpdir()`). Разъезд настоящий и назван в теле PR заметкой
// на фазу 4; зашей эталон целиком в `/tmp` — и он краснел бы на подменённом
// TMPDIR там, где код прав.
const STATE = '/tmp';
const TMP = os.tmpdir();

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
    CRAFT_METRICS_LOG: paths.metricsLog(),
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
