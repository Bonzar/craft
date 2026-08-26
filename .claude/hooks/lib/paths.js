// Пути к файлам состояния, которыми хуки разговаривают друг с другом.
//
// Правило резолва у всех одно и держится на защите от протечки: путь берётся из
// переопределения окружения, иначе выводится из НЕПУСТОГО идентификатора сессии,
// а при пустом идентификаторе файла нет вовсе. Общий default делили бы между
// собой параллельные headless-прогоны, и периметр одной сессии открывал бы
// запись другой.
//
// Имена файлов менять нельзя: пока слой переезжает, эти же файлы читают и пишут
// хуки, оставшиеся на bash. Здесь они собраны в одном месте именно поэтому —
// разъехавшаяся строчка в одном хуке тихо рвёт связку. Эталон набора — список
// герметичных путей в раннере кейсов (tests/run.js).
import os from 'node:os';
import path from 'node:path';

export function sessionId() {
  return process.env.CLAUDE_CODE_SESSION_ID || '';
}

// Путь, который существует только при непустом идентификаторе сессии: им
// адресуется периметр записи, и общий default открывал бы чужую сессию.
function perSession(envName, name) {
  const override = process.env[envName];
  if (override) return override;
  const sid = sessionId();
  return sid ? `/tmp/${name.replace('{sid}', sid)}` : '';
}

// Путь, у которого общий default законен: файл держит счётчик или метку, а не
// периметр записи, и разделение его между сессиями ничего не открывает.
function perSessionOrDefault(envName, name) {
  const override = process.env[envName];
  if (override) return override;
  return `/tmp/${name.replace('{sid}', sessionId() || 'default')}`;
}

// Периметр одобренного плана и четыре его спутника.
export function planGateMarker() {
  return perSession('CRAFT_PLAN_GATE_MARKER', 'craft-plan-gate.{sid}.approved');
}
function besideMarker(suffix) {
  const marker = planGateMarker();
  return marker ? `${marker}.${suffix}` : '';
}
export function approvedPlans() {
  return besideMarker('plans');
}
export function buttonPlans() {
  return besideMarker('button-plans');
}
export function permissionWindow() {
  return besideMarker('qa-window');
}
export function classifierDegraded() {
  return besideMarker('classifier-degraded');
}

// Файл плана этой сессии: путь к нему лежит в метке, которую пишет свой хук.
export function planFileMarker() {
  return perSessionOrDefault('CRAFT_PLAN_FILE_MARKER', 'plan-file.{sid}.path');
}

// Обкатка плана критиком: отметка, ожидание, счётчик кругов и память версии.
export function planCriticMarker() {
  return perSessionOrDefault('CRAFT_PLAN_CRITIC_MARKER', 'plan-critic.{sid}.done');
}
export function planCriticPending() {
  return perSessionOrDefault('CRAFT_PLAN_CRITIC_PENDING', 'plan-critic.{sid}.pending');
}
export function planCriticRuns() {
  return perSessionOrDefault('CRAFT_PLAN_CRITIC_RUNS', 'plan-critic.{sid}.runs');
}
export function planCriticRound() {
  return perSessionOrDefault('CRAFT_PLAN_CRITIC_ROUND', 'plan-critic.{sid}.round');
}

// Дельта планов: хеши юнитов последнего одобренного и его текст рядом.
export function planDeltaStore() {
  return perSessionOrDefault('CRAFT_PLAN_DELTA_STORE', 'plan-delta.{sid}.hashes');
}
export function planDeltaSnapshot() {
  return `${planDeltaStore()}.snapshot`;
}

// Метки хода: показанный план и служебный ход.
export function planShownMarker() {
  return perSessionOrDefault('CRAFT_PLAN_SHOWN_MARKER', 'plan-shown.{sid}');
}
export function serviceTurnMarker() {
  return perSessionOrDefault('CRAFT_SERVICE_TURN_MARKER', 'plan-service-turn.{sid}');
}

// Инцидентный контур: буфер наблюдений и взвод закрытия.
export function observeBuffer() {
  return perSessionOrDefault('OBSERVE_BUFFER', 'agent-observe.{sid}.log');
}
export function incidentClosureMarker() {
  return perSessionOrDefault('INCIDENT_CLOSURE_MARKER', 'incident-closure.{sid}.armed');
}

// Якорь сессии: задача в базе, выбранная Владом на старте. Путь строится по
// НЕПУСТОМУ идентификатору сессии — общий адрес открывал бы одной сессии якорь
// другой, а гвард на нём решает, можно ли писать.
export function sessionAnchor() {
  return perSession('SESSION_ANCHOR_STATE', 'session-anchor.{sid}');
}

// Прочее состояние.
export function factGateStateDir() {
  return process.env.FACT_GATE_STATE_DIR || '/tmp';
}
export function routineFactsMarker() {
  return perSessionOrDefault('ROUTINE_FACTS_MARKER', 'routine-facts.{sid}.reminded');
}
// Синк системы берёт идентификатор сессии из САМОГО события, а не из окружения:
// он работает и там, где переменной нет, — поэтому сессия передаётся аргументом.
export function syncSystemState(sid) {
  const override = process.env.SYNC_SYSTEM_STATE;
  if (override) return override;
  return path.join(os.tmpdir(), `sync-system.${sid || sessionId() || 'default'}`);
}
export function relativeLinkState() {
  const override = process.env.RELATIVE_LINK_STATE;
  if (override) return override;
  return path.join(os.tmpdir(), `relative-link.${sessionId() || 'default'}.blocked`);
}

// Каталог меток уступки второму вызову события.
export function hookOnceDir() {
  return process.env.HOOK_ONCE_DIR || os.tmpdir();
}

// Отладочный след последнего входа: по нему проверяются факты о составе события
// (например, наличие поля режима разрешений) без правки харнесса.
export function lastInputTrace(kind) {
  const sid = sessionId() || 'default';
  return `/tmp/${kind}-last-input.${sid}.json`;
}

// Кэш предодобренной зоны прямого редактирования. Место одно и каноническое —
// чекаут, в котором лежит РЕАЛЬНЫЙ файл хука: сборщик кэша считает его по той же
// формуле, поэтому облако, локальные воркри, arc-маунты и сессии по расписанию
// сходятся на одном файле.
export function exemptScopeFile() {
  return process.env.CRAFT_GATE_EXEMPT_SCOPE
    || path.join(repoRootOf(import.meta.url), '.claude', 'craft-gate-exempt-scope.txt');
}

// Корень чекаута, в котором лежит САМ файл хука: по нему резолвятся его спутники
// (классификатор, кэш предодобренной зоны). Считать от рабочего каталога нельзя —
// сессия чужого проекта запускает хук совсем из другого места.
export function repoRootOf(moduleUrl) {
  const file = new URL(moduleUrl).pathname;
  return path.resolve(path.dirname(file), '..', '..', '..');
}
