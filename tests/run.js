#!/usr/bin/env node
// Регресс-тесты хуков Claude Code (.claude/hooks/). Раннер набора кейсов
// один в один: тот же формат кейсов, те же исходы, те же смоуки, тот же отчёт.
//
// Кейс (tests/hooks/*.jsonl) — один JSON-объект на строку:
//   {"name","hook","input":{…событие…},"expect":"deny|allow|ask|block|inject|silent|contains:<строка>"}
// Раннер подаёт `input` хуку на stdin и проверяет исход:
//   deny   — stdout с permissionDecision "deny"
//   allow  — хук НЕ отказал и не заблокировал (гварды на проходе молчат)
//   ask    — stdout с permissionDecision "ask"
//   block  — stdout с decision "block" (стоп-хуки)
//   inject — stdout несёт директиву инцидента
//   silent — stdout пуст
//   contains:<строка> / not-contains: / err-contains: / err-not-contains:
// Exit 0 — все кейсы зелёные И каждый исход каждого хука покрыт; иначе 1.
//
// ДВА ЯЗЫКА. Хук резолвится по имени БЕЗ расширения: сначала .js, затем .sh.
// Пока идёт перенос слоя на JS, обе версии лежат рядом, и один и тот же набор
// кейсов принимает ту, что есть. Тем же правилом идут шаги подготовки кейса.
//
// ДИФФЕРЕНЦИАЛЬНЫЙ РЕЖИМ (--diff): кейс прогоняется ОБЕИМИ версиями хука на
// одном входе и в раздельном состоянии, и любое расхождение — ответа, stderr
// или оставшихся после прогона файлов состояния — считается падением. Нужен он
// потому, что сами кейсы эталоном не являются: ожидание `deny` смотрит только
// на вердикт и не смотрит на текст причины, а текст причины и есть продукт
// хука. Режим живёт ровно до сноса bash-версий.
//
// Внешние наборы хуков (напр. локальный яндекс-слой в ~/.claude, вне git):
//   EXTRA_HOOKS_DIR=~/.claude/hooks EXTRA_CASES_DIR=~/.claude/tests/hooks node tests/run.js
//
// Зависимости: только node. Запуск из любого каталога.

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const REPO = path.resolve(__dirname, '..');
const HOOKS = path.join(REPO, '.claude', 'hooks');
const CASES_DIR = path.join(REPO, 'tests', 'hooks');
const SETTINGS = path.join(REPO, '.claude', 'settings.json');
const EXTRA_HOOKS_DIR = process.env.EXTRA_HOOKS_DIR || '';
const EXTRA_CASES_DIR = process.env.EXTRA_CASES_DIR || '';
const DIFF = process.argv.includes('--diff');

// UTF-8-локаль обязательна для bash-хуков: часть дефектов видна ТОЛЬКО в ней. В
// bash подстановка `$var` вплотную к не-ASCII символу в UTF-8 читается как имя
// вместе с этим символом и валит скрипт по set -u, а в локали C та же строка
// работает. Из-за этого сломанный guard-plan-delta прошёл ревью: CI был зелёный,
// а на рабочей машине гвард молча падал.
const BASE_ENV = { ...process.env, LC_ALL: 'C.UTF-8' };

// Ключ кейса → файл хука без расширения. Незнакомый ключ резолвится по имени
// самого ключа, поэтому карта нужна только там, где они расходятся.
const SCRIPT = {
  'guard-craft-markdown': 'craft-guard-markdown',
  'guard-plan-hygiene': 'craft-guard-plan-hygiene',
  'detect-incident': 'universal-detect-incident',
  'guard-plan-gate': 'universal-guard-plan-gate',
  'plan-gate-approve': 'universal-plan-gate-approve',
  'plan-gate-reset': 'universal-plan-gate-reset',
  'sleep-waiter-guard': 'universal-sleep-waiter-guard',
  'config-protection': 'universal-config-protection',
  'block-no-verify': 'universal-block-no-verify',
  'fact-gate': 'universal-fact-gate',
  'stop-routine-facts': 'universal-stop-routine-facts',
  'session-anchor': 'universal-session-anchor',
  'guard-plan-critic': 'universal-guard-plan-critic',
  'guard-plan-delta': 'universal-guard-plan-delta',
  'guard-plan-service-turn': 'universal-guard-plan-service-turn',
  'guard-plan-exit-failure': 'universal-guard-plan-exit-failure',
  'mark-plan-critic': 'universal-mark-plan-critic',
  'mark-plan-file': 'universal-mark-plan-file',
  'plan-gate-button': 'universal-plan-gate-button',
  'guard-critic-plateau': 'universal-guard-critic-plateau',
  'plan-delta': 'universal-guard-plan-delta',
  'stop-incident-closure': 'universal-stop-incident-closure',
  'stop-relative-link': 'universal-stop-relative-link',
  'detect-incident-arm': 'universal-detect-incident',
};

// Каждый хук обязан хоть раз показать каждый свой исход: кейс-набор, где у
// гварда нет ни одного deny, доказывает только то, что хук молчит.
const REQUIRED = [
  'guard-craft-markdown:deny', 'guard-craft-markdown:allow',
  'guard-plan-hygiene:deny', 'guard-plan-hygiene:allow',
  'detect-incident:inject', 'detect-incident:silent',
  'guard-plan-gate:deny', 'guard-plan-gate:allow',
  'sleep-waiter-guard:deny', 'sleep-waiter-guard:allow',
  'config-protection:deny', 'config-protection:allow',
  'block-no-verify:deny', 'block-no-verify:allow',
  'fact-gate:deny', 'fact-gate:allow',
  'stop-routine-facts:block', 'stop-routine-facts:silent',
  'guard-plan-critic:deny', 'guard-plan-critic:allow',
  'guard-critic-plateau:deny', 'guard-critic-plateau:allow',
  'guard-plan-delta:deny', 'guard-plan-delta:allow',
  'guard-plan-service-turn:deny', 'guard-plan-service-turn:allow',
  'mark-plan-critic:silent', 'mark-plan-file:silent',
  'stop-incident-closure:block', 'stop-incident-closure:silent',
  'stop-relative-link:block', 'stop-relative-link:silent',
  'session-anchor:deny', 'session-anchor:allow',
];

// Файлы каталога, которые хуками не являются: диспетчер с его таблицей
// маршрутов — сам механизм регистрации.
const REVERSE_WHITELIST = ['dispatch', 'dispatch-table'];

// --- запуск хуков ------------------------------------------------------------

// Файл хука по имени без расширения: JS предпочитается, bash — фолбек. Внешний
// набор (EXTRA_HOOKS_DIR) идёт после репозиторного тем же правилом.
function resolveHook(base, ext) {
  const dirs = EXTRA_HOOKS_DIR ? [HOOKS, EXTRA_HOOKS_DIR] : [HOOKS];
  const exts = ext ? [ext] : ['.js', '.sh'];
  for (const dir of dirs) {
    for (const e of exts) {
      const p = path.join(dir, base + e);
      if (fs.existsSync(p)) return p;
    }
  }
  return '';
}

// Хук запускается своим интерпретатором: bash-файлы через bash (исполняемый бит
// им не нужен), JS — текущим node, чтобы прогон не зависел от того, что лежит в
// PATH у тестов.
function runHook(script, input, env, args = []) {
  const isJs = script.endsWith('.js');
  const cmd = isJs ? process.execPath : 'bash';
  return spawnSync(cmd, [script, ...args], {
    input,
    env,
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
  });
}

// --- состояние кейса ---------------------------------------------------------

let tmpSeq = 0;
function tmpName(prefix) {
  tmpSeq += 1;
  return path.join(os.tmpdir(), `${prefix}.${process.pid}.${tmpSeq}`);
}

// Герметичное состояние на один прогон: хуки с побочными эффектами (буфер
// наблюдений, маркеры факт-гейта, напоминание рутин) не должны трогать ЖИВУЮ
// сессию, а параллельные прогоны — делить состояние друг с другом.
function makeState() {
  const marker = tmpName('plan-gate-test');
  const fgdir = fs.mkdtempSync(path.join(os.tmpdir(), 'fact-gate-test.'));
  const oncedir = fs.mkdtempSync(path.join(os.tmpdir(), 'hook-once-test.'));
  const icmark = `${tmpName('incident-closure-test')}.armed`;
  const s = {
    marker,
    fgdir,
    oncedir,
    icmark,
    obsbuf: tmpName('observe-buffer-test'),
    rfmark: tmpName('routine-facts-test'),
    planpath: tmpName('plan-file-test'),
    criticmark: tmpName('plan-critic-test'),
    serviceturn: tmpName('plan-service-turn-test'),
    criticpend: tmpName('plan-critic-pending-test'),
    planshown: tmpName('plan-shown-test'),
    criticruns: tmpName('plan-critic-runs-test'),
    relstate: tmpName('relative-link-test'),
    syncstate: tmpName('sync-system-test'),
    classtrace: tmpName('mock-classifier-trace'),
    registry: tmpName('approval-registry-test'),
    anchor: tmpName('session-anchor-test'),
    codexhome: tmpName('codex-home-test'),
    metrics: tmpName('metrics-test'),
  };
  s.env = {
    CRAFT_PLAN_GATE_MARKER: s.marker,
    CRAFT_APPROVAL_REGISTRY: s.registry,
    OBSERVE_BUFFER: s.obsbuf,
    FACT_GATE_STATE_DIR: s.fgdir,
    ROUTINE_FACTS_MARKER: s.rfmark,
    CRAFT_PLAN_FILE_MARKER: s.planpath,
    CRAFT_PLAN_CRITIC_MARKER: s.criticmark,
    INCIDENT_CLOSURE_MARKER: s.icmark,
    CRAFT_SERVICE_TURN_MARKER: s.serviceturn,
    CRAFT_PLAN_CRITIC_PENDING: s.criticpend,
    CRAFT_PLAN_SHOWN_MARKER: s.planshown,
    CRAFT_PLAN_CRITIC_RUNS: s.criticruns,
    RELATIVE_LINK_STATE: s.relstate,
    SYNC_SYSTEM_STATE: s.syncstate,
    SESSION_ANCHOR_STATE: s.anchor,
    // Журнал метрик герметичен у каждого кейса: иначе прогон писал бы в общий
    // журнал /tmp, а кейсы про содержимое журнала читали бы чужие строки.
    CRAFT_METRICS_LOG: s.metrics,
    // Хранение сводок выключено ВСЕГДА: иначе кейс Stop пушил бы в настоящую
    // ветку metrics. Его git-логика проверяется отдельно
    // (tests/metrics-store-git.sh) на временных репозиториях.
    METRICS_STORE: 'off',
    // Дом codex — герметичный у КАЖДОГО кейса, а не только у своих. Хук входа
    // пишет туда файл, и общий дефолт означал бы, что любой стартовый кейс
    // кладёт живой токен в настоящий ~/.codex рабочей машины.
    CODEX_HOME: s.codexhome,
    HOOK_ONCE: 'off',
    HOOK_ONCE_DIR: s.oncedir,
    CRAFT_PLAN_CRITIC_ROUND: tmpName('plan-critic-round-test'),
    // Классификатор гейта в тестах ВСЕГДА мок (дефолтный ответ
    // «СООТВЕТСТВУЕТ»), иначе кейс с периметром сделал бы сетевой вызов
    // настоящей модели. След вызова мока — признак для кейсов «модель не
    // зовётся»: ответ хука одинаков с вызовом и без, различает их только след.
    PLAN_CLASSIFIER_CMD: path.join(CASES_DIR, 'fixtures', 'mock-classifier.sh'),
    MOCK_CLASSIFIER_TRACE: s.classtrace,
  };
  return s;
}

// Файлы состояния после прогона — предмет сверки в дифференциальном режиме:
// ответ хука бывает одинаков, а след на диске разным.
function stateSnapshot(s) {
  const out = {};
  const files = [
    ['marker', s.marker], ['marker.plans', `${s.marker}.plans`],
    ['marker.button-plans', `${s.marker}.button-plans`],
    ['marker.qa-window', `${s.marker}.qa-window`],
    ['marker.classifier-degraded', `${s.marker}.classifier-degraded`],
    ['observe-buffer', s.obsbuf], ['routine-facts', s.rfmark],
    ['plan-file', s.planpath], ['plan-critic', s.criticmark],
    ['incident-closure', s.icmark], ['service-turn', s.serviceturn],
    ['plan-critic-pending', s.criticpend], ['plan-shown', s.planshown],
    ['plan-critic-runs', s.criticruns], ['relative-link', s.relstate],
    ['sync-system', s.syncstate], ['approval-registry', s.registry],
    ['session-anchor', s.anchor],
  ];
  for (const [label, file] of files) {
    if (fs.existsSync(file)) out[label] = fs.readFileSync(file, 'utf8');
  }
  if (fs.existsSync(s.fgdir)) {
    for (const name of fs.readdirSync(s.fgdir).sort()) {
      out[`fact-gate/${name}`] = fs.readFileSync(path.join(s.fgdir, name), 'utf8');
    }
  }
  return out;
}

function cleanState(s) {
  const files = [
    s.marker, `${s.marker}.button-plans`, `${s.marker}.classifier-degraded`,
    `${s.marker}.qa-window`, `${s.marker}.plans`, s.obsbuf, s.rfmark, s.planpath,
    s.criticmark, s.icmark,
    s.icmark.replace(/\.armed$/, '.reminded'), s.serviceturn, s.criticpend,
    s.planshown, s.criticruns, s.env.CRAFT_PLAN_CRITIC_ROUND, s.relstate,
    s.syncstate, s.classtrace, s.registry, s.anchor,
    s.metrics, `${s.metrics}.state.json`, `${s.metrics}.summary.json`,
  ];
  for (const f of files) fs.rmSync(f, { force: true });
  fs.rmSync(s.fgdir, { recursive: true, force: true });
  fs.rmSync(s.oncedir, { recursive: true, force: true });
  // Дом codex убирается обязательно: в нём лежит вход, а хук входа зовётся и из
  // посторонних стартовых кейсов. Не убрать — копии токена копились бы в /tmp
  // после каждого прогона, и на машине разработчика туда осел бы настоящий
  // CODEX_AUTH_JSON, унаследованный от окружения.
  fs.rmSync(s.codexhome, { recursive: true, force: true });
}

// --- прогон одного кейса -----------------------------------------------------

function subst(value, s) {
  if (typeof value !== 'string') return value;
  const withDir = value.split('{TESTS_DIR}').join(CASES_DIR);
  // {REGISTRY} — герметичный путь реестра этого прогона. Кейсу он нужен, чтобы
  // указать ASSERT_FILE на тот же файл, куда пишет хук: путь генерится раннером
  // и заранее кейсу неизвестен.
  //
  // {CLASSTRACE} — след вызовов мока классификатора. По нему судят кейсы про
  // МАТЕРИАЛ: что именно хук положил в промпт разбора. По реестру этого не
  // видно — там лежит ответ заглушки, заданный самим кейсом, а не то, что ушло
  // в вопрос.
  //
  // {CODEXHOME} — герметичный дом codex этого прогона: по нему кейс наводит
  // ASSERT_FILE на файл входа, который заводит хук.
  //
  // {METRICS} — журнал метрик этого прогона: кейсы хука метрик судят по его
  // строкам.
  if (!s) return withDir;
  return withDir
    .split('{REGISTRY}').join(s.registry)
    .split('{CLASSTRACE}').join(s.classtrace)
    .split('{CODEXHOME}').join(s.codexhome)
    .split('{METRICS}').join(s.metrics);
}

// Один проход кейса: подготовка, повторы, ответ хука и след на диске. `ext`
// задаёт версию хука (.js/.sh) — в дифференциальном режиме проход делается
// дважды, в раздельном состоянии.
function runPass(c, ext) {
  const base = SCRIPT[c.hook] || c.hook;
  const script = resolveHook(base, ext);
  if (!script) return { missing: true };

  const s = makeState();
  const caseEnv = { ...BASE_ENV, ...s.env };
  for (const [k, v] of Object.entries(c.env || {})) caseEnv[k] = subst(v, s);

  const input = subst(JSON.stringify(c.input ?? {}), s);

  // `arm: true` — предусловие «маркер взведён»: файл, путь которого хук берёт из
  // окружения, создаётся до прогона (взводом в жизни занимается другой хук).
  if (c.arm === true) fs.writeFileSync(s.icmark, '');

  // `registry_seed` — стартовое состояние реестра одобренного: массив целей,
  // который кладётся в {REGISTRY} до прогона. Собирать такое состояние цепочкой
  // подготовительных хуков нельзя: у всех шагов подготовки одно окружение, а
  // значит и один ответ мока, — «завести цель, потом её закрыть» двумя разными
  // ответами не выразить. Кейс, собранный так, тихо получал бы не то
  // предусловие, которое обещает, и зеленел бы независимо от кода.
  if (Array.isArray(c.registry_seed)) {
    fs.writeFileSync(s.registry, `${c.registry_seed.map((g) => JSON.stringify(g)).join('\n')}\n`);
  }

  // `codex_seed` — вход, уже лежащий в доме codex до прогона. Тем же приёмом и
  // по той же причине, что реестр: предусловие «свой вход новее, чем в
  // настройках» подготовительным хуком не выразить, а без него кейс про выбор
  // «переписывать или нет» зеленел бы на любом коде.
  if (typeof c.codex_seed === 'string') {
    fs.mkdirSync(s.codexhome, { recursive: true });
    fs.writeFileSync(path.join(s.codexhome, 'auth.json'), c.codex_seed);
  }

  // Подготовке по умолчанию подаётся ТОТ ЖЕ вход и то же окружение, что целевому
  // хуку; кейс может задать своё событие (setup_input) и свои переменные
  // (setup_env). Список setup_input — свой элемент каждому шагу подготовки.
  const setupEnv = { ...caseEnv };
  for (const [k, v] of Object.entries(c.setup_env || {})) setupEnv[k] = subst(v);
  const setupList = Array.isArray(c.setup_input) ? c.setup_input : null;
  const setupOne = !setupList && c.setup_input !== undefined
    ? subst(JSON.stringify(c.setup_input)) : '';

  (c.setup || []).forEach((name, i) => {
    const sBase = SCRIPT[name] || name;
    const sScript = resolveHook(sBase, ext) || resolveHook(sBase);
    if (!sScript) return;
    let step = setupOne;
    if (setupList) step = subst(JSON.stringify(setupList[i] ?? null));
    runHook(sScript, step && step !== 'null' ? step : input, setupEnv);
  });

  // `repeat: N` — тот же вход подаётся N раз (хуки, которые отказывают один раз:
  // проверяется ответ ПОСЛЕДНЕГО вызова).
  let res = { stdout: '', stderr: '' };
  const repeat = Number(c.repeat || 1);
  const args = Array.isArray(c.args) ? c.args.map(subst) : [];
  for (let i = 0; i < repeat; i += 1) res = runHook(script, input, caseEnv, args);

  // Число записей окна разрешений снимается ДО уборки — его сверяют кейсы
  // вытеснения и фильтра служебных сообщений.
  let qaCount = 0;
  const qaFile = `${s.marker}.qa-window`;
  if (fs.existsSync(qaFile)) {
    qaCount = fs.readFileSync(qaFile, 'utf8').split('\n').filter((l) => l.startsWith('## Запись')).length;
  }
  const traced = fs.existsSync(s.classtrace);
  const trace = traced ? fs.readFileSync(s.classtrace, 'utf8') : '';
  const state = stateSnapshot(s);
  // Файл, по которому кейс судит о записи (исходы file-contains), тоже след
  // прогона: обе версии хука пишут в один путь, и без снимка расхождение между
  // ними прошло бы незамеченным — вторая версия просто затирает первую.
  const assertFile = caseEnv.ASSERT_FILE || '';
  if (assertFile) {
    try {
      state['assert-file'] = fs.readFileSync(assertFile, 'utf8');
    } catch {
      state['assert-file'] = '';
    }
  }
  cleanState(s);

  return {
    script,
    out: res.stdout || '',
    err: res.stderr || '',
    qaCount,
    traced,
    trace,
    state,
  };
}

// --- оценка исхода -----------------------------------------------------------

function jsonField(text, pick) {
  try {
    return pick(JSON.parse(text.trim()));
  } catch {
    return undefined;
  }
}
const isDeny = (o) => jsonField(o, (j) => j.hookSpecificOutput?.permissionDecision) === 'deny';
const isAsk = (o) => jsonField(o, (j) => j.hookSpecificOutput?.permissionDecision) === 'ask';
const isBlock = (o) => jsonField(o, (j) => j.decision) === 'block';
const trim = (s) => s.replace(/[ \t\n\r]/g, '');

function grade(expect, out, err, env) {
  // Исход по СОДЕРЖИМОМУ ФАЙЛА: хуки инжекта доставляют тело правил снимком, а
  // не печатью, и по stdout проверить запись нечем. Путь берётся из переменной
  // ASSERT_FILE самого кейса — той же, что кейс отдаёт хуку. Переменной нет —
  // незачёт; нечитаемый файл равен пустому.
  if (expect.startsWith('file-contains:') || expect.startsWith('file-not-contains:')) {
    const file = (env || {}).ASSERT_FILE || '';
    if (!file) return false;
    // Снимок, снятый до уборки прогона, старше чтения с диска: файлы состояния
    // самого прогона к этому моменту уже убраны, и читать их было бы поздно.
    let text = (env || {}).ASSERT_TEXT;
    if (text === undefined) {
      text = '';
      try {
        text = fs.readFileSync(file, 'utf8');
      } catch { /* файла нет — считаем пустым */ }
    }
    const needle = expect.slice(expect.indexOf(':') + 1);
    return expect.startsWith('file-contains:') ? text.includes(needle) : !text.includes(needle);
  }
  if (expect === 'deny') return isDeny(out);
  if (expect === 'allow') return !(isDeny(out) || isAsk(out) || isBlock(out));
  if (expect === 'ask') return isAsk(out);
  if (expect === 'block') return isBlock(out);
  if (expect === 'inject') return out.includes('СИГНАЛ ИНЦИДЕНТА');
  if (expect === 'silent') return trim(out) === '';
  if (expect.startsWith('contains:')) return out.includes(expect.slice('contains:'.length));
  // Часть хуков сообщает служебное в stderr — там же грейдер евалов ищет улику
  // доставки правила. Без отдельной проверки эта половина вывода не покрыта.
  if (expect.startsWith('err-contains:')) return err.includes(expect.slice('err-contains:'.length));
  // Отрицание: иногда доказательство — именно ОТСУТСТВИЕ строки (хук не пошёл по
  // короткому пути, гвард не сработал вхолостую).
  if (expect.startsWith('not-contains:')) return !out.includes(expect.slice('not-contains:'.length));
  if (expect.startsWith('err-not-contains:')) return !err.includes(expect.slice('err-not-contains:'.length));
  return null; // неизвестное ожидание
}

// --- сбор кейсов -------------------------------------------------------------

function caseFiles() {
  const files = fs.existsSync(CASES_DIR)
    ? fs.readdirSync(CASES_DIR).filter((f) => f.endsWith('.jsonl')).sort()
      .map((f) => path.join(CASES_DIR, f))
    : [];
  if (EXTRA_CASES_DIR && fs.existsSync(EXTRA_CASES_DIR)) {
    files.push(...fs.readdirSync(EXTRA_CASES_DIR).filter((f) => f.endsWith('.jsonl')).sort()
      .map((f) => path.join(EXTRA_CASES_DIR, f)));
  }
  return files;
}

// --- смоуки настроек ---------------------------------------------------------

// Поверхность, которую хук объявляет, обязана быть за ним зарегистрирована:
// раннер зовёт хуки по своей карте и регистраций не видит, поэтому пропавший
// матчер тесты иначе не ловят.
const NEEDS_MATCHER = [
  ['universal-guard-plan-gate', 'PreToolUse', 'Bash'],
  ['universal-guard-plan-exit-failure', 'PostToolUseFailure', 'ExitPlanMode'],
  ['universal-guard-plan-service-turn', 'PreToolUse', 'ExitPlanMode'],
  ['universal-session-anchor', 'PreToolUse', 'Bash'],
  ['universal-session-anchor', 'PostToolUse', 'AskUserQuestion'],
];
const DISPATCH = path.join(HOOKS, 'dispatch.js');

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(SETTINGS, 'utf8'));
  } catch {
    return {};
  }
}

function registeredCommands(settings) {
  const out = [];
  for (const groups of Object.values(settings.hooks || {})) {
    for (const group of groups || []) {
      for (const h of group.hooks || []) if (h.command) out.push(h.command);
    }
  }
  return out;
}

function smokeChecks() {
  const smoke = [];
  const settings = readSettings();

  // Каждая зарегистрированная команда обязана существовать и быть исполняемой —
  // ловит регрессии путей после переименований и переносов.
  for (const c of registeredCommands(settings)) {
    const p = c.split('$CLAUDE_PROJECT_DIR').join(REPO);
    if (!fs.existsSync(p)) smoke.push(`missing hook file: ${p}`);
    else {
      try {
        fs.accessSync(p, fs.constants.X_OK);
      } catch {
        smoke.push(`hook not executable: ${p}`);
      }
    }
  }

  // Маршрут спрашивается у самого диспетчера, а не вычитывается из таблицы
  // глазами: сверяется то, кого он ПОЗОВЁТ, а не то, что где-то написано.
  const routed = (event, tool) => {
    const res = spawnSync(process.execPath, [DISPATCH, '--list', event, tool], { encoding: 'utf8' });
    return (res.stdout || '').split('\n').filter(Boolean);
  };
  for (const [base, event, tool] of NEEDS_MATCHER) {
    if (!routed(event, tool).includes(base)) {
      smoke.push(`hook ${base} not routed on ${event} for tool ${tool}`);
    }
  }

  // Обратный смоук: каждый ФАЙЛ хука обязан быть зарегистрирован хотя бы в одном
  // контуре (проектные настройки или установщик) — незарегистрированный хук
  // лежит мёртвым, выглядя установленным.
  // Состав слоя живёт в таблице маршрутов; настройки и установщик держат лишь
  // регистрацию самого диспетчера. Поэтому «зарегистрирован» — значит назван в
  // таблице (или, для переходных случаев, прямо в командах и установщике).
  let registry = registeredCommands(settings).join('\n');
  for (const extra of [path.join(REPO, 'install.sh'), path.join(HOOKS, 'dispatch-table.js')]) {
    try {
      registry += `\n${fs.readFileSync(extra, 'utf8')}`;
    } catch { /* файла нет — смотрим то, что есть */ }
  }

  // Пара «одно имя, два расширения» на время переезда считается одной проверкой:
  // перенесённый JS-хук кладётся рядом с ещё зарегистрированной bash-версией, и
  // требовать ему собственной регистрации значило бы валить прогон на каждом шаге
  // миграции. Поблажка уходит вместе с последней bash-версией.
  const orphans = (files) => files.filter((f) => {
    const base = path.basename(f).replace(/\.(sh|js)$/, '');
    if (REVERSE_WHITELIST.includes(base)) return false;
    // В таблице маршрутов хук назван БЕЗ расширения, в прямых регистрациях — с
    // ним. Считается любое из имён: файл, названный хоть где-то, не мёртв.
    return !(registry.includes(`${base}.sh`)
      || registry.includes(`${base}.js`)
      || registry.includes(`'${base}'`));
  }).map((f) => path.basename(f));

  // Самотест: красный путь обязан быть достижим — вымышленная сирота должна
  // ловиться, иначе сама проверка молча сломалась.
  // Обе формы: поблажка про соседа не должна пропускать имя, которого нет в
  // регистрациях ни с одним расширением.
  for (const probe of ['zz-selftest-orphan.sh', 'zz-selftest-orphan.js']) {
    if (orphans([path.join(HOOKS, probe)]).join('') !== probe) {
      smoke.push(`reverse-smoke self-test failed: fictitious orphan not caught (${probe})`);
    }
  }
  const hookFiles = fs.readdirSync(HOOKS)
    .filter((f) => f.endsWith('.sh') || f.endsWith('.js'))
    .map((f) => path.join(HOOKS, f));
  for (const b of orphans(hookFiles)) {
    smoke.push(`orphan hook (not registered in settings.json or install.sh): ${b}`);
  }

  smoke.push(...utf8GlueChecks());
  return smoke;
}

// Статическая проверка bash-файлов: подстановка `$var` вплотную к не-ASCII
// символу — дефект. В UTF-8-локали bash читает имя вместе с этим символом и
// скрипт падает по set -u, а в локали C та же строка работает — из-за чего
// сломанный гвард однажды сидел в main с зелёной сборкой. Фикс — скобки.
// Уезжает вместе с последним bash-файлом слоя.
function utf8Glue(files) {
  const hits = [];
  for (const file of files) {
    let text;
    try {
      text = fs.readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    text.split('\n').forEach((line, i) => {
      if (!/\$[A-Za-z_][A-Za-z0-9_]*[^ -~]/.test(line)) return;
      if (/^\s*#/.test(line)) return;                       // строка-комментарий
      const before = line.slice(0, line.indexOf('$'));
      if (before.endsWith('\\')) return;                    // экранированный \$
      const quotes = (before.match(/'/g) || []).length;
      if (quotes % 2 === 1) return;                         // внутри одинарных кавычек
      hits.push(`${file}:${i + 1}`);
    });
  }
  return hits;
}

function utf8GlueChecks() {
  const smoke = [];
  const probe = path.join(os.tmpdir(), `utf8-glue-selftest.${process.pid}`);
  const D = '$'; const Q = '«'; const QQ = '»';
  fs.writeFileSync(probe, `x="${Q}${D}t${QQ} "\n`);
  if (utf8Glue([probe]).length === 0) {
    smoke.push('utf8-glue self-test failed: planted defect not caught');
  }
  // Обратная сторона: скобки, ASCII-кавычка после имени, комментарий, одинарные
  // кавычки и экранированный `\$` законны и флагаться не должны.
  fs.writeFileSync(probe, [
    `a="${Q}${D}{t}${QQ} "`,
    `b="${D}name"`,
    `# note: ${D}t${QQ} label`,
    `echo '${D}t${QQ} literal'`,
    `echo "\\${D}t${QQ} escaped"`,
    '',
  ].join('\n'));
  const legal = utf8Glue([probe]);
  if (legal.length > 0) {
    smoke.push(`utf8-glue self-test failed: legal forms flagged: ${legal.join(' ')}`);
  }
  fs.rmSync(probe, { force: true });

  const dirs = [HOOKS, path.join(REPO, 'tests'), path.join(REPO, 'evals'), path.join(REPO, 'evals', 'lib')];
  const files = [];
  for (const dir of dirs) {
    if (!fs.existsSync(dir)) continue;
    for (const f of fs.readdirSync(dir)) {
      if (f.endsWith('.sh')) files.push(path.join(dir, f));
    }
  }
  if (fs.existsSync(path.join(REPO, 'install.sh'))) files.push(path.join(REPO, 'install.sh'));
  for (const hit of utf8Glue(files)) {
    smoke.push(`$var glued to a non-ASCII char (use \${var}): ${hit}`);
  }
  return smoke;
}

// --- прогон ------------------------------------------------------------------

function main() {
  const files = caseFiles();
  if (files.length === 0) {
    process.stderr.write(`ERROR: no case files in ${CASES_DIR}\n`);
    process.exit(2);
  }

  let total = 0; let pass = 0; let fail = 0;
  const fails = [];
  const covered = new Set();

  const row = (result, hook, expect, name) => {
    process.stdout.write(
      `${result.padEnd(6)} ${String(hook).padEnd(22)} ${String(expect).padEnd(7)} ${name}\n`,
    );
  };
  row('RESULT', 'HOOK', 'EXPECT', 'NAME');
  process.stdout.write(`${'-'.repeat(75)}\n`);

  for (const file of files) {
    const lines = fs.readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, idx) => {
      if (line.trim() === '' || line.startsWith('#')) return;
      total += 1;
      let c;
      try {
        c = JSON.parse(line);
      } catch {
        fail += 1;
        fails.push(`${path.basename(file)}:${idx + 1} — invalid JSON`);
        row('FAIL', '?', '?', `${path.basename(file)}:${idx + 1} invalid JSON`);
        return;
      }

      covered.add(`${c.hook}:${c.expect}`);
      const r = runPass(c);
      if (r.missing) {
        fail += 1;
        fails.push(`${c.hook} / ${c.name} — unknown hook or missing script`);
        row('FAIL', c.hook, c.expect, c.name);
        return;
      }

      let ok = grade(c.expect, r.out, r.err, {
        ASSERT_FILE: subst((c.env || {}).ASSERT_FILE || ''),
        ASSERT_TEXT: r.state['assert-file'],
      });
      let got = r.out;
      if (ok === null) {
        fails.push(`${c.hook} / ${c.name} — unknown expect '${c.expect}'`);
        ok = false;
      }

      // Ассерты следа классификатора: ответ хука в этих исходах одинаков,
      // различает их только след мока.
      if (ok && c.assert_no_model_call === true && r.traced) {
        ok = false; got = 'классификатор был вызван, а не должен';
      }
      if (ok && c.assert_model_call === true && !r.traced) {
        ok = false; got = 'классификатор не был вызван, а должен';
      }
      if (ok && c.assert_trace_contains && !r.trace.includes(c.assert_trace_contains)) {
        ok = false; got = `в промпте классификатора нет «${c.assert_trace_contains}»`;
      }
      if (ok && c.assert_qa_records !== undefined
          && String(r.qaCount) !== String(c.assert_qa_records)) {
        ok = false; got = `записей в окне разрешений: ${r.qaCount}, ожидалось ${c.assert_qa_records}`;
      }

      // Дифференциальный режим: вторая версия того же хука обязана ответить тем
      // же — и тем же следом на диске.
      if (ok && DIFF) {
        const base = SCRIPT[c.hook] || c.hook;
        const other = r.script.endsWith('.js') ? '.sh' : '.js';
        if (resolveHook(base, other)) {
          const alt = runPass(c, other);
          const diffs = [];
          if (alt.out !== r.out) diffs.push('stdout');
          if (alt.err !== r.err) diffs.push('stderr');
          if (JSON.stringify(alt.state) !== JSON.stringify(r.state)) diffs.push('состояние');
          // След классификатора сверяется содержимым, а не фактом наличия: обе
          // версии могут его вызвать, но по-разному сериализовать правку — и
          // тогда расхождение проехало бы незамеченным.
          if (alt.traced !== r.traced) diffs.push('вызов классификатора');
          else if (alt.trace !== r.trace) diffs.push('промпт классификатора');
          if (diffs.length > 0) {
            ok = false;
            got = `версии разошлись (${diffs.join(', ')}): ${path.basename(r.script)} против ${path.basename(alt.script)}`;
          }
        }
      }

      if (ok) {
        pass += 1;
        row('PASS', c.hook, c.expect, c.name);
      } else {
        fail += 1;
        fails.push(`${c.hook} / ${c.name} — expected ${c.expect}, got: ${trim(String(got).slice(0, 120))}`);
        row('FAIL', c.hook, c.expect, c.name);
      }
    });
  }

  const missing = REQUIRED.filter((k) => !covered.has(k));
  const smoke = smokeChecks();

  process.stdout.write(`${'-'.repeat(75)}\n`);
  if (fails.length > 0) {
    process.stdout.write('Failures:\n');
    for (const m of fails) process.stdout.write(`  - ${m}\n`);
  }
  if (missing.length > 0) {
    process.stdout.write('Uncovered outcomes (each hook must cover each outcome):\n');
    for (const m of missing) process.stdout.write(`  - ${m}\n`);
  }
  if (smoke.length > 0) {
    process.stdout.write('Settings smoke failures:\n');
    for (const m of smoke) process.stdout.write(`  - ${m}\n`);
  }
  process.stdout.write(
    `TOTAL: ${pass}/${total} passed; ${fail} failed; uncovered outcomes: ${missing.length}; settings smoke: ${smoke.length}\n`,
  );
  process.exit(fail === 0 && missing.length === 0 && smoke.length === 0 ? 0 : 1);
}

main();
