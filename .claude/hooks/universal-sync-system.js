#!/usr/bin/env node
// Доставка правок системы в ЖИВУЮ сессию. Регистрируется на два события:
//
//   Stop             — конец хода: опрос сети (fetch + сборка свежего снимка
//                      правил Craft) и отчёт на диск. Здесь Влада никто не
//                      заставляет ждать, поэтому сеть живёт именно тут.
//   UserPromptSubmit — отправка сообщения: применение готового отчёта. Только
//                      локальная работа — git-операции по уже скачанному и
//                      печать в контекст. Сети на этом пути нет никогда.
//
// Зачем вообще: чекаут сессии режется один раз на старте, снимки правил Craft
// строятся хуками старта. Без этого хука живая сессия работает на коде и
// правилах момента своего рождения, а Владу приходится просить «подтяни main» —
// агенту при этом неоткуда знать, что он отстал.
//
// Цель синка — репа, в которой лежит САМ файл хука (симлинков больше нет, так
// что это всегда настоящий чекаут). Два случая:
//   свой чекаут   — сессия запущена в этой же репе: вливаем main в её ветку,
//                   но только при чистом рабочем дереве; конфликт откатываем.
//   общий чекаут  — репа лишь подключена сессии рабочей директорией: там может
//                   стоять ветка Влада, поэтому вливаем, только если чекаут на
//                   main и чист, иначе двигаем ОДИН указатель main.
//
// Молчит ровно в одном случае — отставания нет. Отказ проверки (нет сети,
// позиция чекаута неопределённая) печатает строку один раз за сессию: молчание
// не должно выглядеть как «всё свежее».
//
// Fail quiet на всём неожиданном: сломанный синк не должен задерживать
// сообщение Влада.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { readEvent } from './lib/event-claude.js';
import { hookOnce } from './lib/once.js';
import { syncSystemState } from './lib/paths.js';

if (process.env.SYNC_SYSTEM === 'off') process.exit(0);
// Автономный прогон (рутина) не мутирует систему под собой на середине, евал —
// не портит кейсы своим выводом.
if (process.env.CRAFT_AUTONOMOUS || process.env.CRAFT_EVAL) process.exit(0);

const selfPath = fileURLToPath(import.meta.url);
let dir = path.dirname(selfPath);
try {
  dir = path.dirname(fs.realpathSync(selfPath));
} catch { /* нечего резолвить — берём каталог как есть */ }

const { raw, core, cwd, harness_event, session_id } = readEvent();
const eventName = harness_event || '';
const sid = session_id || 'default';

// Хук зарегистрирован и project-level, и пользовательски — уступаем второму вызову.
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);

const TARGET = process.env.SYNC_SYSTEM_TARGET || path.resolve(dir, '..', '..');
// Сессия берётся из СОБЫТИЯ, а не из окружения: синк работает и там, где
// переменной сессии нет вовсе.
const STATE = syncSystemState(sid);
const INTERVAL = process.env.SYNC_SYSTEM_INTERVAL ?? '900';
const BUDGET = Number(process.env.SYNC_SYSTEM_DELTA_BUDGET || 5000);

const REPORT = `${STATE}.report`;
const LOCK = `${STATE}.lock`;
const STAMP = `${STATE}.stamp`;
const NOTIFIED = `${STATE}.notified`;
const BASE = `${STATE}.rules-base`;
const FRESH = `${STATE}.rules-fresh`;

// Локаль дочерних команд задана явно: по выводу гита хук принимает решения, а в
// разных локалях он разный.
const CHILD_ENV = { ...process.env, LC_ALL: 'C.UTF-8' };

// git в целевом чекауте. Возвращает код возврата и вывод без хвостовых
// переводов строки — ровно то, что видел шелл через подстановку команды.
function g(...args) {
  const res = spawnSync('git', ['-C', TARGET, ...args], {
    encoding: 'utf8', env: CHILD_ENV, maxBuffer: 64 * 1024 * 1024,
  });
  return { ok: res.status === 0, out: (res.stdout || '').replace(/\n+$/, '') };
}

function exists(file) {
  try {
    return fs.statSync(file).size > 0;
  } catch {
    return false;
  }
}

// Соседний хук любой из двух версий: пока слой переезжает, рядом лежат обе.
function sibling(base) {
  for (const ext of ['.js', '.sh']) {
    const file = path.join(dir, base + ext);
    if (fs.existsSync(file)) return file;
  }
  return '';
}
function runScript(file, extraEnv = {}) {
  const runner = file.endsWith('.js') ? process.execPath : 'bash';
  return spawnSync(runner, [file], {
    env: { ...CHILD_ENV, ...extraEnv }, stdio: 'ignore',
  });
}

// ---------------------------------------------------------------- сетевой шаг

// Свежий снимок правил Craft — существующим инжектором, нацеленным на свой файл
// (у него для этого своя переменная). Свою копию логики загрузки не заводим.
function buildRulesSnapshot() {
  const injector = sibling('craft-inject-router');
  if (!injector) return false;
  runScript(injector, { CRAFT_ROUTER_SNAPSHOT: `${FRESH}.tmp` });
  if (exists(`${FRESH}.tmp`)) {
    try {
      fs.renameSync(`${FRESH}.tmp`, FRESH);
      return true;
    } catch { /* не переименовалось — снимка нет */ }
  }
  try {
    fs.rmSync(`${FRESH}.tmp`, { force: true });
  } catch { /* нечего сносить */ }
  return false;
}

function probe() {
  const now = Math.floor(Date.now() / 1000);
  let last = 0;
  try {
    const stored = fs.readFileSync(STAMP, 'utf8').trim();
    if (/^[0-9]+$/.test(stored)) last = Number(stored);
  } catch { /* метки времени ещё нет */ }
  if (/^[0-9]+$/.test(INTERVAL) && Number(INTERVAL) > 0 && now - last < Number(INTERVAL)) return;

  // Замок: один сетевой прогон за раз, иначе два fetch дерутся за .git/index.lock.
  try {
    fs.mkdirSync(LOCK);
  } catch {
    return;
  }
  try {
    try {
      fs.writeFileSync(STAMP, `${now}\n`);
    } catch { /* без метки интервал просто не сработает */ }

    const tmp = `${REPORT}.tmp`;
    let report = '';
    const finish = () => {
      try {
        fs.writeFileSync(tmp, report);
        fs.renameSync(tmp, REPORT);
      } catch { /* отчёт не лёг — применять будет нечего */ }
    };

    const headBefore = g('rev-parse', 'HEAD').out;
    const branch = g('rev-parse', '--abbrev-ref', 'HEAD').out;
    if (!headBefore || !branch || branch === 'HEAD') {
      report += 'probe_error=позиция чекаута неопределённая\n';
      finish();
      return;
    }

    if (!g('fetch', '--quiet', 'origin', 'main').ok) {
      report += 'probe_error=нет сети\n';
      finish();
      return;
    }

    // Клон в облаке обрезанный: без общего предка вливание не соберётся.
    if (g('rev-parse', '--is-shallow-repository').out === 'true') {
      if (!g('merge-base', 'HEAD', 'origin/main').ok) {
        g('fetch', '--quiet', '--deepen=200', 'origin', 'main');
      }
    }

    let ahead = g('rev-list', '--count', 'HEAD..origin/main').out;
    if (!/^[0-9]+$/.test(ahead)) ahead = '0';

    const resolve = (p) => {
      try {
        return fs.realpathSync(p);
      } catch {
        return '';
      }
    };
    const scope = resolve(process.env.CLAUDE_PROJECT_DIR || '/nonexistent') === resolve(TARGET)
      ? 'own' : 'shared';

    report += `ahead=${ahead}\nscope=${scope}\nbranch=${branch}\nhead_before=${headBefore}\n`;
    if (buildRulesSnapshot()) report += `rules=${FRESH}\n`;
    finish();
  } finally {
    try {
      fs.rmSync(LOCK, { recursive: true, force: true });
    } catch { /* замок уже снят */ }
  }
}

// ------------------------------------------------------------ применение

function dirty() {
  return g('status', '--porcelain').out !== '';
}

// Раскладывает подтянутые файлы по тому, доедут ли они в живую сессию сами.
function changedReport(headBefore) {
  const live = [];
  const watched = [];
  const cold = [];
  const other = [];
  for (const file of g('diff', '--name-only', headBefore, 'HEAD').out.split('\n')) {
    if (!file) continue;
    if (/^\.claude\/(hooks|skills|agents|commands)\//.test(file)) live.push(file);
    else if (file === '.claude/settings.json') watched.push(file);
    else if (file === 'CLAUDE.md' || file === '.claude/CLAUDE.md' || file.startsWith('.claude/rules/')) cold.push(file);
    else other.push(file);
  }

  let msg = '';
  if (live.length) msg += ` Уже действуют (читаются с диска при срабатывании): ${live.join(' ')}.`;
  if (watched.length) msg += ' Изменилась регистрация хуков — её подхватывает файл-вотчер.';
  if (cold.length) msg += ` НЕ доехало в контекст: ${cold.join(' ')} — файл на диске новый, контекст старый, перечитай его, прежде чем на него опираться.`;
  if (other.length) msg += ` Прочее: ${other.join(' ')}.`;
  return msg;
}

// Пересборка бинарника craft-sync, если приехали его исходники: иначе исходник
// новый, а инструмент на PATH старый.
function maybeRebuildSync(headBefore) {
  const changed = g('diff', '--name-only', headBefore, 'HEAD').out.split('\n');
  if (!changed.some((f) => f.startsWith('craft-sync/'))) return;
  const builder = sibling('craft-build-sync');
  if (!builder) return;
  runScript(builder, { CRAFT_SYNC_BUILD: '1' });
}

// Регистрация хуков живёт в пользовательских настройках: без переустановки новый
// хук приезжает файлом, о котором никто не знает.
function maybeReinstall() {
  const installer = path.join(TARGET, 'install.sh');
  if (!fs.existsSync(installer)) return;
  spawnSync('bash', [installer], { env: CHILD_ENV, stdio: 'ignore' });
}

function applyCode(aheadFromReport, scope) {
  // Между сетевым шагом и этим применением цель могла переключить ветку или
  // уехать вперёд сама. Поэтому ветка, позиция и само отставание читаются
  // ЗАНОВО — счёт локальный, по уже скачанному origin/main, сети не требует.
  // Из отчёта берётся только то, чего заново не узнать.
  const branch = g('rev-parse', '--abbrev-ref', 'HEAD').out;
  const head = g('rev-parse', 'HEAD').out;
  if (!branch || branch === 'HEAD' || !head) {
    process.stdout.write(`🔄 Свежесть системы проверить не удалось: позиция чекаута ${TARGET} неопределённая. Считай, что сессия может работать на устаревших правилах.\n`);
    return;
  }
  let ahead = g('rev-list', '--count', 'HEAD..origin/main').out;
  if (!/^[0-9]+$/.test(ahead)) ahead = aheadFromReport;
  // Отставания уже нет (цель подтянули руками, соседняя сессия) — это законное
  // единственное молчание.
  if (Number(ahead) === 0) return;

  // Перечень изменившихся файлов считается от позиции ПЕРЕД вливанием, а не от
  // записанной в отчёте: после смены ветки та дала бы всю разницу между ветками.
  const headBefore = head;
  let reason = '';
  let ok = false;

  if (scope === 'own') {
    if (dirty()) {
      reason = `в рабочем дереве ${TARGET} несохранённые изменения`;
    } else if (g('merge', '--no-edit', 'origin/main').ok) {
      ok = true;
    } else {
      g('merge', '--abort');
      reason = 'вливание упёрлось в конфликт и откачено';
    }
  } else if (branch !== 'main') {
    // Рабочее дерево общего чекаута не трогаем вовсе — двигаем один указатель.
    g('fetch', '--quiet', 'origin', 'main:main');
    reason = `${TARGET} стоит не на main`;
  } else if (dirty()) {
    reason = `в рабочем дереве ${TARGET} несохранённые изменения`;
  } else if (g('merge', '--ff-only', 'origin/main').ok) {
    ok = true;
  } else {
    reason = `перемотка ${TARGET} не удалась`;
  }

  if (ok) {
    maybeRebuildSync(headBefore);
    if (scope === 'shared') maybeReinstall();
    process.stdout.write(`🔄 Система обновлена: подтянуто ${ahead} коммитов main в ${TARGET}.${changedReport(headBefore)}\n`);
  } else {
    process.stdout.write(`🔄 Система ушла вперёд на ${ahead} коммитов main, подтянуть нельзя: ${reason}. Сессия работает на устаревших правилах и хуках. Разберись с этим чекаутом и подтяни main, прежде чем опираться на системные правила.\n`);
  }
}

// Снимок правил, который импортировала САМА сессия: ищется от каталога, который
// событие принесло с собой, вверх до первого `.claude` со снимком. Переменной с
// корнем проекта тут нет — в окружении хука на неё полагаться нельзя.
function sessionSnapshot() {
  if (!cwd) return '';
  let probeDir;
  try {
    if (!fs.statSync(cwd).isDirectory()) return '';
    probeDir = fs.realpathSync(cwd);
  } catch {
    return '';
  }
  while (probeDir && probeDir !== '/') {
    const candidate = path.join(probeDir, '.claude', 'craft-router-context.md');
    if (exists(candidate)) return candidate;
    probeDir = path.dirname(probeDir);
  }
  return '';
}

// Зона правил снимка: без служебного заголовка со временем сборки (иначе отличие
// всегда) и без регенерируемой памяти (её переписывают рутины актуализации).
function rulesZone(file) {
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return '';
  }
  const kept = [];
  for (const line of text.split('\n')) {
    if (/^=== Craft: роутер/.test(line)) continue;
    if (line.includes('<pageTitle>🧠 Память (регенерируемая)</pageTitle>')) break;
    kept.push(line);
  }
  // Хвостовой перевод строки был и у awk: он печатает по строке на запись.
  return kept.length ? `${kept.join('\n')}\n` : '';
}

function tempFile(prefix) {
  return path.join(os.tmpdir(), `${prefix}.${process.pid}.${Math.floor(Date.now() / 1000)}`);
}

function applyRules(fresh) {
  if (!exists(fresh)) return;

  // Базы нет — засеиваем снимком СТАРТА ЭТОЙ сессии: именно он попал в контекст
  // через импорт её CLAUDE.md. Снимок из чекаута цели за базу не берётся — в
  // сессии другого проекта это чужой файл произвольного возраста, и первая же
  // дельта показала бы правила, которых в контексте не было. Своего снимка нет
  // (сессия без роутера, сбой сети на старте) — засеиваем свежим и молчим:
  // сравнивать не с чем, а обрезок всего роутера вместо дельты бесполезен.
  if (!exists(BASE)) {
    const atStart = sessionSnapshot();
    try {
      fs.copyFileSync(atStart && exists(atStart) ? atStart : fresh, BASE);
    } catch { /* база не засеялась — следующая дельта попробует снова */ }
    if (!atStart || !exists(atStart)) return;
  }

  const a = tempFile('sync-rules-a');
  const b = tempFile('sync-rules-b');
  const zoneA = rulesZone(BASE);
  const zoneB = rulesZone(fresh);
  try {
    fs.writeFileSync(a, zoneA);
    fs.writeFileSync(b, zoneB);
  } catch {
    return;
  }

  const done = () => {
    for (const file of [a, b]) {
      try {
        fs.rmSync(file, { force: true });
      } catch { /* временный файл переживёт прогон */ }
    }
    try {
      fs.copyFileSync(fresh, BASE);
    } catch { /* база не обновилась — дельта повторится */ }
  };

  if (zoneA === zoneB) {
    done();
    return;
  }

  // Дельта — тем же внешним инструментом, что и раньше: формат унифицированного
  // диффа читает и человек, и модель, а своя реализация дала бы другой текст.
  const diff = spawnSync('diff', ['-u', a, b], {
    encoding: 'utf8', env: CHILD_ENV, maxBuffer: 64 * 1024 * 1024,
  });
  const body = (diff.stdout || '').split('\n').slice(2).join('\n');
  const delta = Buffer.from(body, 'utf8').subarray(0, BUDGET).toString('utf8');
  done();

  // Публичный снимок, который импортирует CLAUDE.md, подменяем атомарно: его
  // читает и импорт при компакте, и детектор инцидентов на том же событии.
  const publicSnapshot = path.join(TARGET, '.claude', 'craft-router-context.md');
  if (fs.existsSync(path.join(TARGET, '.claude'))) {
    try {
      fs.copyFileSync(fresh, `${publicSnapshot}.tmp`);
      fs.renameSync(`${publicSnapshot}.tmp`, publicSnapshot);
    } catch { /* публичный снимок остался прежним */ }
  }

  process.stdout.write(`🧠 Правила Craft изменились с начала сессии. Дельта ниже; она обрезана по бюджету, полный текст — в снимке правил на диске: он уже обновлён, но сам в контекст не вернётся. Дальше действуй по свежей версии правила, а не по той, что в контексте выше.\n${delta}\n`);
}

function apply() {
  if (!exists(REPORT)) return;

  // Забираем отчёт атомарно: второй вызов (двойная регистрация, повторное
  // событие) уже ничего не найдёт и промолчит.
  const taken = `${REPORT}.taken`;
  let text;
  try {
    fs.renameSync(REPORT, taken);
    text = fs.readFileSync(taken, 'utf8');
  } catch {
    return;
  }
  try {
    fs.rmSync(taken, { force: true });
  } catch { /* остаток отчёта безвреден */ }

  const field = (name) => {
    const line = text.split('\n').find((l) => l.startsWith(`${name}=`));
    return line ? line.slice(name.length + 1) : '';
  };

  const probeError = field('probe_error');
  if (probeError) {
    // Один раз за сессию: повторять на каждом сообщении незачем, а промолчать нельзя.
    if (!fs.existsSync(NOTIFIED)) {
      try {
        fs.writeFileSync(NOTIFIED, '');
      } catch { /* без метки строка придёт ещё раз */ }
      process.stdout.write(`🔄 Свежесть системы проверить не удалось: ${probeError}. Считай, что сессия может работать на устаревших правилах.\n`);
    }
    return;
  }

  let ahead = field('ahead');
  if (!/^[0-9]+$/.test(ahead)) ahead = '0';
  if (Number(ahead) > 0) applyCode(ahead, field('scope') || 'own');

  const rules = field('rules');
  if (rules) applyRules(rules);
}

// База снимается при ПЕРВОМ же событии сессии, не дожидаясь первого применения:
// так окно, в которое старт соседней сессии успевает переписать общий снимок,
// самое узкое. Совсем закрыть его нельзя — снимок один на чекаут.
if (!exists(BASE)) {
  const atStart = sessionSnapshot();
  if (atStart && exists(atStart)) {
    try {
      fs.copyFileSync(atStart, BASE);
    } catch { /* база засеется при применении */ }
  }
}

if (eventName === 'Stop' || eventName === 'SubagentStop') {
  if (process.env.SYNC_SYSTEM_WORKER_INLINE) {
    probe();
  } else {
    // Ход уже закончен, ждать сеть некому — уходим в фон. Дедупликация в
    // работнике выключена: событие уже занято этим вызовом, и повторная сверка
    // отменила бы саму работу.
    const worker = spawn(process.execPath, [selfPath], {
      detached: true,
      stdio: ['pipe', 'ignore', 'ignore'],
      env: { ...process.env, SYNC_SYSTEM_WORKER_INLINE: '1', HOOK_ONCE: 'off' },
    });
    worker.stdin.end(raw);
    worker.unref();
  }
} else if (eventName === 'UserPromptSubmit') {
  apply();
}
