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

// Каталог состояния — ОДНА формула на весь слой, и он же поле `state_dir`
// канонического события. Ту же формулу обязан считать и bash-веер критика: две
// формулы означают, что отметку пишут в один каталог, а читают из другого.
export function stateDir() {
  return process.env.CRAFT_STATE_DIR || os.tmpdir();
}

// Идентификатор сессии кладёт ОБЁРТКА: имя переменной харнеса знает она, сюда
// значение приходит под своим именем. Без обёртки (дочерний процесс, ручной
// запуск) переменной нет — и это законно, путь тогда просто не строится.
//
// Читают его отсюда только пути БЕЗ аргумента: там, куда событие доходит, сессия
// приходит значением (metricsLog, decisionLog, syncSystemState), и второго канала
// у них нет — иначе один и тот же путь считался бы то по событию, то по
// окружению, и разъехался бы ровно там, где они разошлись.
export function sessionId() {
  return process.env.CRAFT_SESSION_ID || '';
}

// Путь, который существует только при непустом идентификаторе сессии: им
// адресуется периметр записи, и общий default открывал бы чужую сессию.
function perSession(envName, name) {
  const override = process.env[envName];
  if (override) return override;
  const sid = sessionId();
  return sid ? path.join(stateDir(), name.replace('{sid}', sid)) : '';
}

// Путь, у которого общий default законен: файл держит счётчик или метку, а не
// периметр записи, и разделение его между сессиями ничего не открывает.
function perSessionOrDefault(envName, name) {
  const override = process.env[envName];
  if (override) return override;
  return path.join(stateDir(), name.replace('{sid}', sessionId() || 'default'));
}

// Реестр одобренного: цели и задачи, в которые раскладывается всё, на что Влад
// дал ок. Правило строгое, как у периметра, а не как у счётчиков: реестр держит
// одобрения, и общий default открыл бы записи одной сессии другой.
export function approvalRegistry() {
  return perSession('CRAFT_APPROVAL_REGISTRY', 'craft-approvals.{sid}.jsonl');
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

// Метки хода: показанный план и служебный ход.
export function planShownMarker() {
  return perSessionOrDefault('CRAFT_PLAN_SHOWN_MARKER', 'plan-shown.{sid}');
}
export function serviceTurnMarker() {
  return perSessionOrDefault('CRAFT_SERVICE_TURN_MARKER', 'plan-service-turn.{sid}');
}

// Журнал событий сессии: строка на каждое чтение и каждую запись, плюс сигналы
// хода. Правило СТРОГОЕ, как у периметра, а не как у счётчиков: по журналу
// читатели судят, что эта сессия читала и писала, и общий default дал бы одной
// сессии право править по чтению другой.
//
// Он же — факт `journal` канонического события: путь кладёт обёртка, прочитав
// событие, а хук, которому журнал нужен, объявляет факт списком и на его
// отсутствие отвечает `unsupported` с именем.
export function journalLog() {
  return perSession('CRAFT_JOURNAL_LOG', 'journal.{sid}.jsonl');
}

// Инстинкт-контур держит ДВА РАЗНЫХ факта в двух разных файлах, и это разделение
// принципиально.
//
// Метка — акт АГЕНТА: «разбор сделан». Содержимого у неё нет и оно не читается:
// значим только сам факт существования.
//
// Состояние — знание ХУКА: докуда сигналы уже выданы на разбор и докуда он выдал
// их в последней директиве. Пишет его ТОЛЬКО хук и только про себя.
//
// Слить их в один файл нельзя. Тогда горизонт двигал бы гейтимый: агент,
// упёршийся в блокировку конца хода, положил бы в метку заведомо большое число и
// заглушил бы контур до конца сессии — не злым умыслом, а «помогая». Гейтимый не
// пишет собственное предусловие; он может только сказать «сделал».
export function instinctFlushMarker() {
  return perSessionOrDefault('INSTINCT_FLUSH_MARKER', 'instinct-flush.{sid}.done');
}
export function instinctFlushState() {
  return perSessionOrDefault('INSTINCT_FLUSH_STATE', 'instinct-flush.{sid}.state');
}

// Инцидентный контур: взвод закрытия разбора.
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
  return process.env.FACT_GATE_STATE_DIR || stateDir();
}
export function routineFactsMarker() {
  return perSessionOrDefault('ROUTINE_FACTS_MARKER', 'routine-facts.{sid}.reminded');
}
// Синк системы берёт идентификатор сессии из САМОГО события, а не из окружения:
// он работает и там, где переменной нет, — поэтому сессия передаётся аргументом.
export function syncSystemState(sid) {
  const override = process.env.SYNC_SYSTEM_STATE;
  if (override) return override;
  return path.join(stateDir(), `sync-system.${sid || 'default'}`);
}
export function relativeLinkState() {
  const override = process.env.RELATIVE_LINK_STATE;
  if (override) return override;
  return path.join(stateDir(), `relative-link.${sessionId() || 'default'}.blocked`);
}

// Журнал метрик сессии: события JSONL, по строке на событие хука; рядом с ним
// хук держит своё состояние (`<журнал>.state.json`). Идентификатор берётся из
// СОБЫТИЯ, как у синка системы: в окружении хука переменной сессии может не
// быть. Общий default законен — журнал держит числа и имена, а не периметр.
export function metricsLog(sid) {
  const override = process.env.CRAFT_METRICS_LOG;
  if (override) return override;
  return path.join(stateDir(), `metrics.${sid || 'default'}.jsonl`);
}

// Журнал решений: канал от решателя к наблюдателю. Решение пишет тот, кто решает
// (decide.js), наблюдатель переносит строки в свой журнал (decision-log.js). Файл
// один на сессию и ОБЩИЙ с пакетами; строки различаются ключом события и его
// именем — по этой паре их и сшивают (lib/metrics-summary.js).
export function decisionLog(sid, dir = '') {
  const override = process.env.CRAFT_DECISION_LOG;
  if (override) return override;
  // Каталог берётся ИЗ СОБЫТИЯ, когда оно его принесло: `state_dir` — поле ядра,
  // и канал решений резолвится по нему, а не по своей копии формулы.
  return path.join(dir || stateDir(), `decisions.${sid || 'default'}.jsonl`);
}

// Каталог меток уступки второму вызову события.
export function hookOnceDir() {
  return process.env.HOOK_ONCE_DIR || stateDir();
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
