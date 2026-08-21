// Имена файлов состояния — инвариант переезда: пока часть хуков на bash, а часть
// на JS, они разговаривают через одни и те же файлы, и разъехавшаяся строчка
// рвёт связку молча. Поэтому тест не сверяет модуль сам с собой: он вытаскивает
// дефолты ИЗ bash-хуков и требует, чтобы модуль давал то же самое.
//
// Расширение .mjs, а не .js: в каталоге тестов нет манифеста модулей, и .js
// читался бы как обычный скрипт, которому импорт недоступен.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HOOKS = path.join(REPO, '.claude', 'hooks');
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

// Дефолты из bash-хуков: `${ENV:-/tmp/имя.${sid}.суффикс}`.
function bashDefaults() {
  const found = new Map();
  for (const file of fs.readdirSync(HOOKS).filter((f) => f.endsWith('.sh'))) {
    const text = fs.readFileSync(path.join(HOOKS, file), 'utf8');
    // Дефолт бывает с вложенной подстановкой (`${TMPDIR:-/tmp}`,
    // `${CLAUDE_CODE_SESSION_ID:-default}`), поэтому вложенные скобки входят в
    // захват: без них эталон обрезался бы на первой закрывающей и сверка шла бы
    // с половиной строки.
    const re = /\$\{([A-Z_]+):-((?:[^{}]|\$\{[^{}]*\})*)\}/g;
    let m = re.exec(text);
    while (m) {
      const [, name, raw] = m;
      // Та же запись служит и проверкой «переменная задана» (`${VAR:-}`), и
      // путём по умолчанию. Сверять есть смысл только второе.
      const isPath = raw.startsWith('/tmp') || raw.startsWith('${TMPDIR');
      if (isPath && !found.has(name)) found.set(name, raw);
      m = re.exec(text);
    }
  }
  return found;
}

// Как bash развернул бы дефолт при заданной сессии.
function expand(raw) {
  return raw
    .replace(/\$\{TMPDIR:-\/tmp\}/g, '/tmp')
    .replace(/\$\{CLAUDE_CODE_SESSION_ID:-default\}/g, SID)
    .replace(/\$\{sid\}/g, SID)
    .replace(/\$sid\b/g, SID);
}

test('пути состояния совпадают с дефолтами bash-хуков', async () => {
  const paths = await freshPaths();
  const byEnv = {
    CRAFT_PLAN_GATE_MARKER: paths.planGateMarker(),
    CRAFT_PLAN_FILE_MARKER: paths.planFileMarker(),
    CRAFT_PLAN_CRITIC_MARKER: paths.planCriticMarker(),
    CRAFT_PLAN_CRITIC_PENDING: paths.planCriticPending(),
    CRAFT_PLAN_CRITIC_RUNS: paths.planCriticRuns(),
    CRAFT_PLAN_CRITIC_ROUND: paths.planCriticRound(),
    CRAFT_PLAN_DELTA_STORE: paths.planDeltaStore(),
    CRAFT_PLAN_SHOWN_MARKER: paths.planShownMarker(),
    CRAFT_SERVICE_TURN_MARKER: paths.serviceTurnMarker(),
    OBSERVE_BUFFER: paths.observeBuffer(),
    INCIDENT_CLOSURE_MARKER: paths.incidentClosureMarker(),
    ROUTINE_FACTS_MARKER: paths.routineFactsMarker(),
    SYNC_SYSTEM_STATE: paths.syncSystemState(),
    RELATIVE_LINK_STATE: paths.relativeLinkState(),
  };

  const defaults = bashDefaults();
  let checked = 0;
  for (const [name, raw] of defaults) {
    if (!(name in byEnv)) continue;
    assert.equal(byEnv[name], expand(raw), `дефолт ${name} разошёлся с bash-версией`);
    checked += 1;
  }
  // Сам тест обязан различать исходы: если регулярка перестанет находить
  // дефолты, он молча пройдёт на пустом наборе.
  assert.ok(checked >= 10, `сверено всего ${checked} путей — эталон не разобрался`);
});

test('переопределение окружением сильнее дефолта', async () => {
  const paths = await freshPaths({ CRAFT_PLAN_GATE_MARKER: '/tmp/своя-метка' });
  assert.equal(paths.planGateMarker(), '/tmp/своя-метка');
  assert.equal(paths.approvedPlans(), '/tmp/своя-метка.plans');
});

test('при пустой сессии периметра нет вовсе', async () => {
  const paths = await freshPaths({ CLAUDE_CODE_SESSION_ID: '' });
  assert.equal(paths.planGateMarker(), '', 'общий default открыл бы периметр чужой сессии');
  assert.equal(paths.approvedPlans(), '');
  // А счётчики и метки общий default переживают: они ничего не открывают.
  assert.equal(paths.planCriticRuns(), '/tmp/plan-critic.default.runs');
});
