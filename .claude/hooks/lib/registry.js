// Реестр одобренного: одна структура вместо пяти файлов состояния. Всё, на что
// Влад дал ок — одобренный план, ответ на кнопочный вопрос, прямая реплика, —
// ложится сюда целями, а цель состоит из задач.
//
// Цель — то, что Влад окнул одним куском; её заголовок дословен и служит
// признаком тождества при повторном одобрении. Задача — часть работы под целью;
// своего якоря у неё нет, задачи замещаются вместе со своей целью.
//
// Лог живёт на ЦЕЛИ, а не на задаче: по нему следующая правка видит соседей по
// всей работе, а не только по своему файлу, — это и есть ответ на «гейт не видит
// соседних правок хода».
//
// Формат — строка JSON на цель. Многострочные тексты (реплики, дословные цитаты
// из плана) в самодельный строковый формат не лезут: первая же реплика с
// переводом строки порвала бы запись.
//
// Глушилка CRAFT_REGISTRY=off выключает запись целиком, как у соседних
// автоматических контуров. Чтение при этом работает: выключенная запись не
// повод соврать про уже накопленное.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawn } from 'node:child_process';

// Лог обрезается сверху: длинный ход иначе растит реестр без предела, а он
// целиком уходит в каждую сверку.
export const LOG_KEEP = 20;

function off() {
  return process.env.CRAFT_REGISTRY === 'off';
}

// Чтение никогда не бросает: реестр читают гейт и дельта, и упавшее чтение
// закрыло бы работу целиком. Нечитаемая строка пропускается — потерять одну
// запись дешевле, чем потерять файл.
export function readRegistry(file) {
  if (!file) return [];
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return [];
  }
  const goals = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      const goal = JSON.parse(line);
      if (goal && typeof goal === 'object') goals.push(revive(goal));
    } catch { /* битая строка — пропускаем, файл остаётся цел */ }
  }
  return goals;
}

// Реестр живёт файлом в /tmp и переживает обновление кода: сессия, начатая до
// того, как надгробия сняли, продолжается уже новой версией. Похороненная цель
// оживает на чтении — иначе ровно в такой сессии повторился бы дефект, ради
// которого надгробия и снимали: материал про продолжение работы отсеивался бы
// как дубль, а сверка отказывала бы «работа уже закрыта».
function revive(goal) {
  return goal.state === 'tombstone' ? { ...goal, state: 'live' } : goal;
}

// Лок на ЦИКЛ правки: сама запись атомарна переименованием, а «прочитал —
// поправил — записал» вокруг неё нет. Два параллельных хука читали одно
// состояние, и второй затирал правку первого: терялись строки лога и закрытие
// задач. Каталог — атомарная примитивная блокировка на любой файловой системе:
// mkdir либо создал, либо застал чужой.
//
// Занят — ЖДЁМ, а не пропускаем: пропущенная запись роняет ту работу, ради
// которой лок и берётся. Своего потолка у ожидания нет; снимается только лок,
// брошенный упавшим процессом, — по возрасту каталога.
const LOCK_STALE_MS = 300000;

function lockDir(file) {
  return `${file}.lock`;
}

function takeLock(file) {
  const dir = lockDir(file);
  for (;;) {
    try {
      fs.mkdirSync(dir);
      return dir;
    } catch (err) {
      if (err && err.code !== 'EEXIST') return '';
      let age = 0;
      try {
        age = Date.now() - fs.statSync(dir).mtimeMs;
      } catch {
        continue; // лок исчез между попыткой и замером — пробуем снова
      }
      if (age > LOCK_STALE_MS) {
        try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* уже снят */ }
        continue;
      }
      try {
        execFileSync('sleep', ['0.05'], { stdio: 'ignore' });
      } catch {
        return '';
      }
    }
  }
}

function freeLock(dir) {
  if (!dir) return;
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch { /* лок не снялся — его добьёт следующий по возрасту */ }
}

// withLock(файл, действие) — единственная точка взятия лока. Вложенные вызовы
// лок повторно НЕ берут: иначе дописывание задач, сделанное поверх общей правки,
// клинило бы само себя.
let held = false;

function withLock(file, run) {
  if (held) return run();
  const dir = takeLock(file);
  held = true;
  try {
    return run();
  } finally {
    held = false;
    freeLock(dir);
  }
}

// Запись атомарная: временный файл рядом и переименование. Соседняя сессия или
// параллельный хук читают либо прежний реестр, либо новый, но не половину.
function writeRegistry(file, goals) {
  if (!file || off()) return;
  const body = goals.map((goal) => JSON.stringify(goal)).join('\n');
  const tmp = `${file}.tmp.${process.pid}`;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(tmp, body ? `${body}\n` : '');
    fs.renameSync(tmp, file);
  } catch {
    try { fs.rmSync(tmp, { force: true }); } catch { /* и убрать не вышло */ }
  }
}

// Слепок содержания цели: по нему отличается ревизия от перепоказа. В слепок
// идёт то, что одобряется, — текст и тела задач; состояние и лог не идут, иначе
// перепоказ того же плана читался бы как изменение.
//
// У ЗАКРЫТОЙ задачи в слепок идёт только имя: тело у неё снято закрытием, и
// сравнение с телом из перепоказанного плана всегда расходилось бы — тот же план
// читался бы ревизией и сбрасывал закрытое вместе с логом. Цена: правка тела уже
// закрытой задачи ревизией не считается, но работа под ней и так кончилась.
// Тело закрытой задачи остаётся в файле, поэтому сравнивать её с телом из
// перепоказанного плана есть чем: сравнение идёт напрямую, одинаково у закрытых
// и открытых. Перепоказ того же плана сходится и закрытое не сбрасывается, а
// настоящая ревизия тела расходится и отменяет прежнюю редакцию.
function sameContent(stored, fresh) {
  const slice = (goal) => JSON.stringify({
    text: goal.text || '',
    tasks: (goal.tasks || []).map((t) => ({
      title: t.title || '',
      where: t.where || [],
      body: t.body || '',
    })),
  });
  return slice(stored) === slice(fresh);
}

function normalize(goal) {
  return {
    title: goal.title || '',
    source: goal.source || 'plan',
    state: 'live',
    text: goal.text || '',
    log: [],
    tasks: (goal.tasks || []).map((t, i) => ({
      n: i + 1,
      title: t.title || '',
      where: t.where || [],
      body: t.body || '',
      state: 'open',
    })),
  };
}

// Добавить цель или заместить прежнюю с тем же заголовком. Содержание не
// изменилось — не трогаем вовсе: перепоказ плана штатен, а замещение сбросило бы
// закрытые задачи и лог. Изменилось — прежняя редакция отменяется целиком:
// ревизия не продолжает старое, она его заменяет.
export function upsertGoal(file, goal) {
  if (!file || off() || !goal || !goal.title) return;
  withLock(file, () => {
    const goals = readRegistry(file);
    const fresh = normalize(goal);
    const at = goals.findIndex((g) => g.title === fresh.title);
    if (at === -1) {
      goals.push(fresh);
    } else {
      if (sameContent(goals[at], fresh)) return;
      goals[at] = fresh;
    }
    writeRegistry(file, goals);
  });
}

// Дописать задачи к существующей цели. Это ответ на «моя реплика про ту же
// цель, но новую задачу»: материал про уже одобренную работу не заводит вторую
// такую же цель, а пополняет её. Совпавшая по заголовку задача не дублируется.
//
// Цель с закрытыми задачами принимает новые наравне с прочими: работа под целью
// продолжается — доработка, ответ ревьюеру, обслуживание PR, — и новая задача
// возвращает цель в работу сама. Терминального состояния у цели нет: закрытие
// её задач необратимым не бывает.
export function addTasks(file, at, tasks) {
  patch(file, at, (goal) => {
    const have = new Set((goal.tasks || []).map((t) => t.title));
    let n = (goal.tasks || []).length;
    for (const task of tasks) {
      if (have.has(task.title)) continue;
      n += 1;
      goal.tasks = [...(goal.tasks || []), {
        n,
        title: task.title,
        where: task.where || [],
        body: task.body || '',
        state: 'open',
      }];
    }
  });
}

// Цель адресуется ПОЗИЦИЕЙ, а не заголовком: заголовки повторяются — один и тот
// же план законно одобряется дважды, — и поиск по имени сажал задачу или запись
// лога на первую совпавшую, то есть на уже завершённую работу.
function patch(file, at, change) {
  if (!file || off()) return;
  const i = Number(at);
  if (!Number.isInteger(i) || i < 0) return;
  withLock(file, () => {
    const goals = readRegistry(file);
    if (!goals[i]) return;
    change(goals[i]);
    writeRegistry(file, goals);
  });
}

// Строка лога — задача, файл, суть. Суть приходит от сверки: она и так зовёт
// модель на каждой правке и видит её целиком, поэтому отдельного вызова нет.
export function appendLog(file, at, entry) {
  if (!entry) return;
  patch(file, at, (goal) => {
    goal.log = [...(goal.log || []), entry].slice(-LOG_KEEP);
  });
}

// --- Рубильник ---------------------------------------------------------------
// Режим «проверки сняты» живёт ЗАПИСЬЮ в реестре, а не переменной окружения:
// так он переживает перезапуск, виден в тексте отказа и его нельзя включить
// незаметно. Гаснет со сменой сессии сам — реестр у каждой сессии свой.
//
// Ставит его только тап Влада по кнопке; агент себе рубильник не выдаёт.
export const SWITCH_TITLE = 'Проверки сняты по тапу Влада';

export function switchOn(file) {
  if (!file || off()) return;
  withLock(file, () => {
    const goals = readRegistry(file);
    if (goals.some((g) => g.title === SWITCH_TITLE && g.state === 'live')) return;
    goals.push({
      title: SWITCH_TITLE,
      source: 'switch',
      state: 'live',
      text: 'Влад снял проверки тапом. Правки идут без сверки и пишутся в лог этой записи.',
      log: [],
      tasks: [],
    });
    writeRegistry(file, goals);
  });
}

export function switchOff(file) {
  if (!file || off()) return;
  withLock(file, () => {
    const goals = readRegistry(file).filter((g) => g.title !== SWITCH_TITLE);
    writeRegistry(file, goals);
  });
}

// Индекс живого рубильника или -1. Индекс, а не булево: под этой же записью
// ведётся лог пропущенных правок — след того, что делалось при снятых
// проверках, обязан остаться.
export function switchAt(goals) {
  return goals.findIndex((g) => g.title === SWITCH_TITLE && g.state === 'live');
}

// --- Закрытие ----------------------------------------------------------------
// Реестр чистится закрытием, а не порогом: каждая запись заведена под конкретную
// работу и уходит потому, что работа кончилась.

// Адрес задачи — «Ц‹цель›.‹задача›»: цель по позиции в файле, задача по номеру
// внутри неё. Разбор общий для пульта, приёма и хука лога.
export function parseAddress(address) {
  const at = /^Ц(\d+)\.(\d+)$/i.exec(String(address).trim());
  if (!at) return null;
  return { goal: Number(at[1]) - 1, task: Number(at[2]) };
}

// Закрыть задачи по адресам. Строка цели НЕ удаляется даже когда закрыто всё:
// цель адресуется номером по позиции в файле, и удаление сдвинуло бы нумерацию —
// метка лога, выданная сверкой минуту назад, села бы на чужую цель.
//
// Закрывает агент — по смыслу сделанного, а не по следу в логе. Доказательство
// правки тут не спрашивается: тот, кто закрывает, и есть тот, кто работал.
export function closeTasks(file, addresses) {
  const done = { closed: [], unknown: [] };
  if (!file || off()) {
    done.unknown = [...addresses];
    return done;
  }
  return withLock(file, () => closeUnderLock(file, addresses, done));
}

function closeUnderLock(file, addresses, done) {
  const goals = readRegistry(file);
  let touched = false;

  for (const address of addresses) {
    const at = parseAddress(address);
    const goal = at ? goals[at.goal] : undefined;
    const task = goal ? (goal.tasks || []).find((t) => t.n === at.task) : undefined;
    if (!task) {
      done.unknown.push(address);
      continue;
    }
    // Тело и адреса закрытой задачи остаются на месте: реестр держит не только
    // разрешения, но и историю сессии — что было одобрено, что сделано и где.
    // Из текста для модели тело закрытой задачи всё равно уходит (см. render),
    // поэтому сохранение истории объём сверки не растит.
    task.state = 'closed';
    done.closed.push(address);
    touched = true;
  }

  // Цель, у которой закрылась последняя задача, никуда не девается и остаётся
  // принимающей: работа под ней может продолжиться — доработка, ответ
  // ревьюеру, обслуживание PR, — и новая задача вернёт цель в работу.
  if (touched) writeRegistry(file, goals);
  return done;
}

// Запустить приём материала ФОНОМ: ход Влада не ждёт модель. Метка ставится
// синхронно, до отпускания процесса, — иначе сверка правки успела бы прочитать
// реестр раньше, чем узнала бы, что разбор идёт.
export function ingestInBackground(file, source, text) {
  if (!file || off() || !/\S/.test(text || '')) return;
  const id = `${process.pid}-${Date.now()}`;
  const mark = markParsing(file, id);
  if (!mark) return;
  try {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'registry-ingest-'));
    const material = path.join(dir, 'material.txt');
    fs.writeFileSync(material, text);
    const here = path.dirname(fileURLToPath(import.meta.url));
    const helper = path.resolve(here, '..', '..', '..', 'tools', 'registry-ingest.mjs');
    if (!fs.existsSync(helper)) { unmarkParsing(mark); return; }
    // Тестам нужен детерминированный порядок: фоновый приём допишет реестр
    // когда-нибудь, а кейс проверяет файл сразу. В жизни режим не включается —
    // иначе ход Влада ждал бы модель.
    if (process.env.CRAFT_REGISTRY_SYNC) {
      execFileSync(process.execPath, [helper, source, material, file, id], { stdio: 'ignore' });
      return;
    }
    const child = spawn(process.execPath, [helper, source, material, file, id], {
      detached: true,
      stdio: 'ignore',
    });
    child.unref();
  } catch {
    unmarkParsing(mark);
  }
}

// --- Метки идущего разбора ---------------------------------------------------
// Разбор реплики и кнопочного ответа идёт ФОНОМ: ход Влада не должен ждать
// модель. Но сверка правки обязана видеть уже полный реестр — иначе правка,
// которую он только что разрешил, упрётся в гейт, потому что разрешение ещё не
// доехало. Поэтому каждый идущий разбор оставляет метку, а сверка ждёт, пока
// меток не останется.
function parsingDir(file) {
  return `${file}.parsing`;
}

// Метка на один разбор. Имя уникально — разборов может идти несколько разом
// (реплика и следом кнопочный ответ).
export function markParsing(file, id) {
  if (!file || off()) return '';
  const mark = path.join(parsingDir(file), String(id));
  try {
    fs.mkdirSync(parsingDir(file), { recursive: true });
    fs.writeFileSync(mark, String(Date.now()));
    return mark;
  } catch {
    return '';
  }
}

export function unmarkParsing(mark) {
  if (!mark) return;
  try {
    fs.rmSync(mark, { force: true });
  } catch { /* метка не снялась — её добьёт потолок ожидания */ }
}

// Ждать, пока идущие разборы допишутся. Своего потолка у ожидания НЕТ: сверка по
// недособранному реестру отклонила бы только что разрешённое, а «подождём немного
// и пойдём» — это ровно тот проход без решения, которого быть не должно. Числа
// здесь — страховка от процесса, умершего насовсем, и они заведомо больше любого
// живого разбора: прежние были втрое короче его собственного бюджета, поэтому
// ожидание сдавалось раньше времени и сносило метку живого разбора как брошенную.
export function waitForParsing(file, capMs = 3600000, stepMs = 200) {
  const until = Date.now() + capMs;
  while (parsingCount(file) > 0 && Date.now() < until) {
    // Пауза без таймеров: хук синхронный, и событийного ожидания чужого
    // процесса здесь нет.
    try {
      execFileSync('sleep', [String(stepMs / 1000)], { stdio: 'ignore' });
    } catch {
      break;
    }
  }
  return parsingCount(file) === 0;
}

export function parsingCount(file, staleMs = 3600000) {
  if (!file) return 0;
  let names = [];
  try {
    names = fs.readdirSync(parsingDir(file));
  } catch {
    return 0;
  }
  let live = 0;
  for (const name of names) {
    const mark = path.join(parsingDir(file), name);
    let started = 0;
    try {
      started = Number(fs.readFileSync(mark, 'utf8')) || 0;
    } catch { /* метка исчезла между чтением каталога и файла */ }
    if (started && Date.now() - started < staleMs) live += 1;
    else unmarkParsing(mark);
  }
  return live;
}

// Читаемый вид для классификатора и для текста отказа. Модель читает то же, что
// человек: отдельный машинный формат для неё разъезжался бы с тем, что видно в
// отказе.
// Цели НУМЕРУЮТСЯ, и адресуются потом номером, а не заголовком. Дедупликация не
// может держаться на том, что модель дословно повторит формулировку: разбирая
// новый материал, она назовёт ту же работу своими словами, и цель задвоится.
// Номер сравнивается точно.
//
// bodies=false печатает скелет — цели и имена задач без тел и логов. Им отвечает
// пульт: агенту нужен адрес, а не 40 000 символов в контекст.
// Номер цели берётся из поля n, когда оно есть: список работы показывает не весь
// реестр, а его живую часть, и нумерация по позиции в отфильтрованном массиве
// адресовала бы закрытие на чужую цель.
// У цели состояния нет: закрытость видна по её задачам, и отдельной пометки
// «работа закрыта» в строке цели не появляется. Пометка была текстовым запретом
// вешать на такую цель новые задачи — и ровно поэтому материал про продолжение
// уже одобренной работы отсеивался как дубль вместо того, чтобы лечь задачей.
//
// Тело и адреса ЗАКРЫТОЙ задачи в текст не идут: в файле они остаются историей
// сессии, но сверке нужен только след того, что работа была и кончилась.
export function render(goals, { bodies = true } = {}) {
  const out = [];
  goals.forEach((goal, i) => {
    const num = Number.isInteger(goal.n) ? goal.n : i + 1;
    out.push(`Ц${num} «${goal.title}» — источник: ${goal.source}`);
    if (bodies && goal.text) out.push(`  текст: ${goal.text}`);
    for (const task of goal.tasks || []) {
      const closed = task.state === 'closed';
      out.push(`  задача Ц${num}.${task.n} «${task.title}» — ${closed ? 'закрыта' : 'открыта'}`);
      if (closed) continue;
      if (task.where && task.where.length) out.push(`    где: ${task.where.join(', ')}`);
      if (bodies && task.body) out.push(`    что: ${task.body}`);
    }
    if (bodies) for (const entry of goal.log || []) out.push(`  сделано: ${entry}`);
  });
  return out.join('\n');
}
