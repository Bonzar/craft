// Общий лок: взаимное исключение и снятие БРОШЕННОГО лока.
//
// Отличать брошенный лок от живого приходится потому, что работа под ним бывает
// заведомо долгой: выгрузка метрик ходит в сеть и гоняет цепочку команд гита, у
// каждой свой потолок в две минуты. По одному возрасту такой лок отбирали бы у
// работающего процесса.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LOCK = path.join(HERE, '..', '..', '.claude', 'hooks', 'lib', 'lock.js');
const lock = await import(LOCK);

const sleep = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

// Песочницы кейсов сносятся ОДНИМ разом в конце файла: у половины из них внутри
// живут чужие процессы и каталоги локов, и убирать их по месту значило бы
// повторить одно и то же в каждом кейсе. Временный каталог здесь же служит
// каталогом состояния хуков, и мусор в нём — не только гигиена.
const sandboxes = [];
function tmpDir(prefix) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  sandboxes.push(dir);
  return dir;
}

after(() => {
  for (const dir of sandboxes) fs.rmSync(dir, { recursive: true, force: true });
});

function waitFor(check, budgetMs = 10000) {
  const started = Date.now();
  while (!check() && Date.now() - started < budgetMs) sleep(20);
  return check();
}

// Держатель лока отдельным процессом: своя занятость лока на процесс, и в одном
// процессе взаимное исключение не проверить.
function holder(dir, file, trace, holdMs) {
  const script = path.join(dir, 'holder.mjs');
  fs.writeFileSync(script, [
    `import { withLock } from ${JSON.stringify(LOCK)};`,
    "import fs from 'node:fs';",
    'const [file, trace, hold] = process.argv.slice(2);',
    'withLock(file, () => {',
    "  fs.appendFileSync(trace, 'A-in\\n');",
    '  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Number(hold));',
    "  fs.appendFileSync(trace, 'A-out\\n');",
    '});',
  ].join('\n'));
  return spawn(process.execPath, [script, file, trace, String(holdMs)], { stdio: 'ignore' });
}

// Лок, который держат ДОЛЬШЕ срока протухания, отбирать нельзя, пока хозяин жив:
// иначе двое работают в критической секции разом, а первый на выходе снимает
// чужой лок и впускает третьего.
test('лок живого процесса не отбирается по возрасту', async () => {
  const dir = tmpDir('lock-live-');
  const file = path.join(dir, 'state');
  const trace = path.join(dir, 'trace');

  const child = holder(dir, file, trace, 1500);
  const exited = new Promise((done) => child.on('exit', done));
  assert.ok(waitFor(() => fs.existsSync(trace)), 'держатель вошёл под лок');

  // Возраст лока состаривается напрямую: ждать в кейсе пять минут нечем, а
  // именно этот случай — долгую работу под локом — и надо проверить.
  const old = new Date(Date.now() - 6 * 60 * 1000);
  fs.utimesSync(`${file}.lock`, old, old);

  const { locked } = lock.withLock(file, () => { fs.appendFileSync(trace, 'B-in\n'); }, { waitMs: 5000 });
  assert.equal(locked, true, 'второй дождался лока в отведённый срок');
  await exited;

  assert.deepEqual(
    fs.readFileSync(trace, 'utf8').trim().split('\n'),
    ['A-in', 'A-out', 'B-in'],
    'второй вошёл только после выхода первого',
  );
});

// Брошенный лок (процесс умер, не сняв) снимается — иначе одно падение запирало
// бы очередь навсегда. Проверяется ЧУЖИМ процессом со сроком: регресс тут — это
// зависание, и в своём процессе кейс висел бы вместе с ним.
test('лок умершего хозяина снимается', () => {
  const dir = tmpDir('lock-dead-');
  const file = path.join(dir, 'state');
  const done = path.join(dir, 'done');
  fs.mkdirSync(`${file}.lock`);
  fs.writeFileSync(path.join(`${file}.lock`, 'owner'), '2147483646');

  const script = path.join(dir, 'taker.mjs');
  fs.writeFileSync(script, [
    `import { withLock } from ${JSON.stringify(LOCK)};`,
    "import fs from 'node:fs';",
    'const [file, done] = process.argv.slice(2);',
    "withLock(file, () => fs.writeFileSync(done, 'ok'));",
  ].join('\n'));
  const child = spawn(process.execPath, [script, file, done], { stdio: 'ignore' });
  const got = waitFor(() => fs.existsSync(done), 15000);
  child.kill('SIGKILL');
  assert.ok(got, 'лок мёртвого хозяина не снялся за отведённый срок');
});

// Ожидание ограничено ВСЕГДА. Прежде хук на брошенном каталоге лока стоял до
// пяти минут, форкая `sleep` каждые 50 мс, — то есть один мусорный каталог
// замораживал ход Влада. Теперь срок вышел — действие не выполняется вовсе, и
// вызывающий об этом узнаёт.
test('лок не достался за срок: действие не выполняется, вызывающий это видит', () => {
  const dir = tmpDir('lock-budget-');
  const file = path.join(dir, 'state');
  fs.mkdirSync(`${file}.lock`);
  fs.writeFileSync(path.join(`${file}.lock`, 'owner'), String(process.pid + 0));
  // Хозяин — «живой» чужой номер: наш собственный pid, которого этот вызов не
  // держит. Лок свежий, отбирать нельзя, ждать бесконечно тоже.
  const started = Date.now();
  let ran = false;
  const { locked, value } = lock.withLock(file, () => { ran = true; return 'значение'; }, { waitMs: 200 });
  const spent = Date.now() - started;
  assert.equal(locked, false, 'исход говорит, что лок не взят');
  assert.equal(ran, false, 'действие не выполнялось');
  assert.equal(value, undefined);
  assert.ok(spent < 3000, `ожидание уложилось в срок: ${spent} мс`);
});

// Абсолютный потолок возраста снимает лок и у ЖИВОГО хозяина: номер процесса
// после перезагрузки переиспользуется, и без потолка состояние запиралось бы
// навсегда — метрики не уезжали бы вообще, а каждый Stop плодил бы ещё один
// вечный фоновый процесс.
test('лок старше абсолютного потолка снимается и при живом хозяине', () => {
  const dir = tmpDir('lock-max-');
  const file = path.join(dir, 'state');
  fs.mkdirSync(`${file}.lock`);
  fs.writeFileSync(path.join(`${file}.lock`, 'owner'), String(process.pid));
  const ancient = new Date(Date.now() - 16 * 60 * 1000);
  fs.utimesSync(`${file}.lock`, ancient, ancient);
  const { locked } = lock.withLock(file, () => 'ок', { waitMs: 200 });
  assert.equal(locked, true, 'вечный лок отобран, работа пошла');
});

// Занятость считается по ПУТЯМ. С одним флагом на процесс вложенный лок на
// другой файл не брался вовсе: второе состояние оставалось без защиты, а
// вызывающий думал, что оно под локом.
test('вложенный лок на другой файл берётся по-настоящему', () => {
  const dir = tmpDir('lock-nested-');
  const outer = path.join(dir, 'outer');
  const inner = path.join(dir, 'inner');
  lock.withLock(outer, () => {
    assert.ok(fs.existsSync(`${outer}.lock`), 'внешний лок взят');
    lock.withLock(inner, () => {
      assert.ok(fs.existsSync(`${inner}.lock`), 'внутренний лок на другом пути тоже взят');
    });
    assert.ok(!fs.existsSync(`${inner}.lock`), 'внутренний снят на выходе');
    const again = lock.withLock(outer, () => 'вложенный на тот же путь', { waitMs: 50 });
    assert.deepEqual(again, { locked: true, value: 'вложенный на тот же путь' },
      'тот же путь повторно не лочится и не клинит сам себя');
  });
  assert.ok(!fs.existsSync(`${outer}.lock`), 'внешний снят');
});

// Отбор брошенного лока приходит с ОПОЗДАНИЕМ: пока второй ждущий решал, что
// хозяин мёртв, первый успел отобрать лок и завести свой, свежий. Второй
// переименует уже ЕГО — и оба окажутся внутри. Проверяется ровно это состояние:
// решение принято по мёртвому хозяину, а на месте уже лок живого.
test('отбор не сносит чужой свежий лок', () => {
  const dir = tmpDir('lock-race-');
  const file = path.join(dir, 'state');
  fs.mkdirSync(`${file}.lock`);
  fs.writeFileSync(path.join(`${file}.lock`, 'owner'), String(process.pid));

  lock.reclaimStale(file, 2147483646);

  assert.ok(fs.existsSync(`${file}.lock`), 'свежий лок остался на месте');
  assert.equal(fs.readFileSync(path.join(`${file}.lock`, 'owner'), 'utf8'), String(process.pid),
    'хозяин свежего лока не потерян');
  assert.deepEqual(fs.readdirSync(dir).filter((n) => n.includes('.stale.')), [],
    'отодвинутый каталог не остался мусором');
});

// Свой случай отбора работает: лок ТОГО хозяина, ради которого затевался отбор,
// снимается целиком.
test('отбор снимает лок названного хозяина', () => {
  const dir = tmpDir('lock-reclaim-');
  const file = path.join(dir, 'state');
  fs.mkdirSync(`${file}.lock`);
  fs.writeFileSync(path.join(`${file}.lock`, 'owner'), '2147483646');

  lock.reclaimStale(file, 2147483646);

  assert.ok(!fs.existsSync(`${file}.lock`), 'брошенный лок снят');
});

// Лок, который НЕ ОТОБРАТЬ, всё равно ограничен сроком. Отбор бывает
// безуспешным навсегда: каталог чужого пользователя в /tmp со sticky-битом не
// переименовать ни разу, а битая ссылка на месте лока не даёт даже возраста —
// и круг «поглядели, отобрать не вышло, пошли снова» шёл бы вечно, вопреки
// инварианту модуля. Проверяется ЧУЖИМ процессом со сроком: регресс здесь —
// это зависание, и в своём процессе кейс висел бы вместе с ним.
test('лок, который не отобрать, не держит вызывающего дольше срока', () => {
  const dir = tmpDir('lock-unreclaimable-');
  const file = path.join(dir, 'state');
  const done = path.join(dir, 'done');
  // Битая ссылка на месте каталога лока: mkdir говорит «занято», а возраст
  // измерить не по чему.
  fs.symlinkSync(path.join(dir, 'no-such-target'), `${file}.lock`);

  const script = path.join(dir, 'waiter.mjs');
  fs.writeFileSync(script, [
    `import { withLock } from ${JSON.stringify(LOCK)};`,
    "import fs from 'node:fs';",
    'const [file, done] = process.argv.slice(2);',
    'const started = Date.now();',
    'const { locked } = withLock(file, () => true, { waitMs: 200 });',
    'fs.writeFileSync(done, JSON.stringify({ locked, spent: Date.now() - started }));',
  ].join('\n'));
  const child = spawn(process.execPath, [script, file, done], { stdio: 'ignore' });
  const got = waitFor(() => fs.existsSync(done), 8000);
  child.kill('SIGKILL');

  assert.ok(got, 'вызов не вернулся за отведённое кейсу время — цикл не ограничен сроком');
  const res = JSON.parse(fs.readFileSync(done, 'utf8'));
  assert.equal(res.locked, false, 'лок не достался, и это сказано исходом');
  assert.ok(res.spent < 5000, `ожидание уложилось в срок: ${res.spent} мс`);
});
