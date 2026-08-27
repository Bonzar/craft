// Реестр одобренного: цели и задачи, в которые раскладывается всё, на что Влад
// дал ок. Тест держит поведение модуля, от которого зависят три хука-источника
// и сверка правки, — форму записи, замещение цели, закрытие, лог и глушилку.
//
// Расширение .mjs, а не .js: в каталоге тестов нет манифеста модулей, и .js
// читался бы как обычный скрипт, которому импорт недоступен.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';

const registry = await import('../../.claude/hooks/lib/registry.js');

function tmpFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'registry-test-')), 'approvals.jsonl');
}

const goal = (over = {}) => ({
  title: '# Юнит 1. Реестр: форма и сборка',
  source: 'plan',
  tasks: [
    { title: 'модуль реестра', where: ['.claude/hooks/lib/registry.js'], body: 'форма записи и команды' },
    { title: 'хук одобрения', where: ['.claude/hooks/universal-plan-gate-approve.js'], body: 'разбор моделью' },
  ],
  ...over,
});

test('цель кладётся с задачами и состоянием по умолчанию', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  const [saved] = registry.readRegistry(file);
  assert.equal(saved.title, '# Юнит 1. Реестр: форма и сборка');
  assert.equal(saved.source, 'plan');
  assert.equal(saved.state, 'live');
  assert.equal(saved.tasks.length, 2);
  assert.equal(saved.tasks[0].state, 'open');
  assert.deepEqual(saved.tasks[0].where, ['.claude/hooks/lib/registry.js']);
});

test('реплика ложится со своим текстом', () => {
  const file = tmpFile();
  registry.upsertGoal(file, { title: 'никогда не создавай .ts', source: 'reply', text: 'никогда не создавай .ts' });
  const [saved] = registry.readRegistry(file);
  assert.equal(saved.source, 'reply');
  assert.equal(saved.text, 'никогда не создавай .ts');
});

test('цель с тем же заголовком и тем же телом не трогается', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.closeTasks(file, ['Ц1.1']);
  registry.appendLog(file, 0, 'задача Ц1.1 · registry.js · завела чтение');

  registry.upsertGoal(file, goal());

  const [saved] = registry.readRegistry(file);
  assert.equal(registry.readRegistry(file).length, 1, 'дубля быть не должно');
  assert.equal(saved.tasks[0].state, 'closed', 'перепоказ того же плана не сбрасывает закрытое');
  assert.equal(saved.log.length, 1, 'и не стирает лог');
});

test('изменившаяся цель замещает прежнюю вместе с задачами и логом', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.closeTasks(file, ['Ц1.1']);
  registry.appendLog(file, 0, 'задача Ц1.1 · registry.js · завела чтение');

  const revised = goal();
  revised.tasks[0].body = 'форма записи, команды и глушилка';
  registry.upsertGoal(file, revised);

  const [saved] = registry.readRegistry(file);
  assert.equal(registry.readRegistry(file).length, 1);
  assert.equal(saved.tasks[0].state, 'open', 'ревизия отменяет прежнюю редакцию, а не продолжает её');
  assert.deepEqual(saved.log, []);
});

test('лог живёт на цели и ограничен сверху', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  for (let i = 1; i <= registry.LOG_KEEP + 5; i += 1) {
    registry.appendLog(file, 0, `задача 1 · registry.js · правка ${i}`);
  }
  const [saved] = registry.readRegistry(file);
  assert.equal(saved.log.length, registry.LOG_KEEP);
  assert.match(saved.log[saved.log.length - 1], /правка 25$/, 'последние записи остаются');
  assert.match(saved.log[0], /правка 6$/, 'старые вытесняются');
});

test('рендер несёт цели, задачи и лог', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.closeTasks(file, ['Ц1.2']);
  registry.appendLog(file, 0, 'задача Ц1.1 · registry.js · завела чтение');
  const text = registry.render(registry.readRegistry(file));
  assert.match(text, /# Юнит 1\. Реестр/);
  assert.match(text, /модуль реестра/);
  assert.match(text, /завела чтение/);
  assert.match(text, /закрыта/, 'закрытая задача видна как закрытая');
});

test('скелет печатает адреса без тел и логов', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.appendLog(file, 0, 'задача Ц1.1 · registry.js · завела чтение');
  const text = registry.render(registry.readRegistry(file), { bodies: false });
  assert.match(text, /задача Ц1\.1 «модуль реестра»/, 'адрес и имя на месте');
  assert.doesNotMatch(text, /форма записи и команды/, 'тела задач не печатаются');
  assert.doesNotMatch(text, /завела чтение/, 'лог не печатается');
});

test('закрытие снимает тело и адреса, имя остаётся', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  const done = registry.closeTasks(file, ['Ц1.1']);
  assert.deepEqual(done.closed, ['Ц1.1']);
  assert.deepEqual(done.unknown, []);

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.tasks[0].state, 'closed');
  assert.equal(saved.tasks[0].title, 'модуль реестра', 'имя нужно, чтобы отказ говорил правду');
  assert.equal(saved.tasks[0].body, '', 'тело закрытой задачи не хранится');
  assert.deepEqual(saved.tasks[0].where, []);
  assert.equal(saved.state, 'live', 'вторая задача открыта — цель ещё живая');
});

test('неизвестный адрес не закрывает ничего и возвращается назад', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  const done = registry.closeTasks(file, ['Ц9.9', 'мусор', 'Ц1.7']);
  assert.deepEqual(done.closed, []);
  assert.deepEqual(done.unknown, ['Ц9.9', 'мусор', 'Ц1.7']);
  assert.equal(registry.readRegistry(file)[0].tasks[0].state, 'open');
});

test('цель со всеми закрытыми задачами становится надгробием', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.appendLog(file, 0, 'задача Ц1.1 · registry.js · завела чтение');
  registry.closeTasks(file, ['Ц1.1', 'Ц1.2']);

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.state, 'tombstone');
  assert.equal(saved.text, '', 'текст цели уходит');
  assert.deepEqual(saved.log, [], 'лог уходит');
  assert.equal(saved.tasks.length, 2, 'имена задач остаются');
  assert.match(registry.render([saved]), /работа закрыта/, 'надгробие видно в рендере');
});

test('на надгробие задачи не вешаются', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.closeTasks(file, ['Ц1.1', 'Ц1.2']);
  registry.addTasks(file, 0, [{ title: 'третья задача', where: [], body: 'тело' }]);
  assert.equal(registry.readRegistry(file)[0].tasks.length, 2, 'надгробие не оживает дописыванием');
});

test('нумерация после закрытия не едет: строка цели остаётся на месте', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.upsertGoal(file, { title: 'вторая цель', source: 'reply', tasks: [{ title: 'её задача' }] });
  registry.closeTasks(file, ['Ц1.1', 'Ц1.2']);

  const goals = registry.readRegistry(file);
  assert.equal(goals.length, 2, 'закрытая цель не удаляется — на ней держится нумерация');
  assert.equal(goals[1].title, 'вторая цель', 'вторая цель осталась второй');
  assert.match(registry.render(goals), /задача Ц2\.1 «её задача»/);
});

// Закрывает агент по смыслу сделанного: следа в логе для этого не требуется —
// раньше без него закрытие не проходило, и работа оставалась вечно открытой.
test('закрытие идёт по адресу и следа в логе не требует', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());

  const done = registry.closeTasks(file, ['Ц1.1']);
  assert.deepEqual(done.closed, ['Ц1.1']);
  assert.deepEqual(done.unknown, []);

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.tasks[0].state, 'closed');
  assert.equal(saved.tasks[1].state, 'open');
  assert.equal(saved.state, 'live');
});

test('битый файл не роняет чтение и не затирается молча', () => {
  const file = tmpFile();
  fs.writeFileSync(file, '{это не json\nтоже не json\n');
  assert.deepEqual(registry.readRegistry(file), [], 'нечитаемые строки пропускаются');
});

test('глушилка выключает запись целиком', () => {
  const file = tmpFile();
  process.env.CRAFT_REGISTRY = 'off';
  try {
    registry.upsertGoal(file, goal());
    assert.equal(fs.existsSync(file), false, 'при глушилке файл не заводится');
  } finally {
    delete process.env.CRAFT_REGISTRY;
  }
});

test('пустой путь ничего не пишет и не падает', () => {
  assert.doesNotThrow(() => registry.upsertGoal('', goal()));
  assert.deepEqual(registry.readRegistry(''), []);
});

// Заголовки целей повторяются: один и тот же план законно одобряется дважды, и
// вторая цель носит то же имя. Пока дописывание искало цель по заголовку, задача
// и запись лога садились на ПЕРВУЮ совпавшую — то есть на уже завершённую работу,
// а живая цель оставалась пустой и не закрывалась.
test('задача садится на цель по позиции, а не на первую с тем же заголовком', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal({ title: 'общий заголовок' }));
  fs.appendFileSync(file, `${JSON.stringify({
    title: 'общий заголовок', source: 'reply', state: 'live', text: '', log: [], tasks: [],
  })}\n`);

  registry.addTasks(file, 1, [{ title: 'третья задача', where: [], body: 'тело' }]);

  const goals = registry.readRegistry(file);
  assert.equal(goals[0].tasks.length, 2, 'первая цель не тронута');
  assert.equal(goals[1].tasks.length, 1, 'задача легла во вторую цель');
  assert.equal(goals[1].tasks[0].title, 'третья задача');
});

test('запись лога садится на цель по позиции', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal({ title: 'общий заголовок' }));
  fs.appendFileSync(file, `${JSON.stringify({
    title: 'общий заголовок', source: 'reply', state: 'live', text: '', log: [], tasks: [],
  })}\n`);

  registry.appendLog(file, 1, 'задача Ц2.1 · registry.js · правка прошла');

  const goals = registry.readRegistry(file);
  assert.deepEqual(goals[0].log, [], 'первая цель без записи');
  assert.equal(goals[1].log.length, 1, 'запись легла во вторую цель');
});

// Лок на цикл правки. Без него два параллельных процесса читают одно состояние и
// второй затирает правку первого: записи теряются молча. Проверяется на ЗАДАЧАХ,
// а не на логе: лог по замыслу хранит только последние записи, и счётчик по нему
// ничего не доказал бы. Одной задачи на процесс мало — окна не накладываются; по
// двадцать подряд накладываются всегда. Кейс идёт со встречным замером: та же
// нагрузка наивной записью обязана терять, иначе он зеленел бы и на сломанном локе.
const WRITERS = 10;
const PER_WRITER = 20;
const EXPECTED = WRITERS * PER_WRITER;

function runWriters(script, file) {
  return Promise.all(Array.from({ length: WRITERS }, (unused, i) => new Promise((done) => {
    spawn(process.execPath, [script, file, String(i)], { stdio: 'ignore' }).on('exit', done);
  })));
}

test('параллельные правки реестра не теряют записей', async () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal({ tasks: [] }));

  const script = path.join(path.dirname(file), 'writer.mjs');
  fs.writeFileSync(script, `
    const registry = await import(${JSON.stringify(path.resolve('.claude/hooks/lib/registry.js'))});
    for (let i = 0; i < ${PER_WRITER}; i += 1) {
      registry.addTasks(process.argv[2], 0, [{ title: 'з' + process.argv[3] + '.' + i, where: [], body: '' }]);
    }
  `);
  await runWriters(script, file);

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.tasks.length, EXPECTED, 'ни одна задача не потеряна');

  // Встречный замер: та же нагрузка наивной записью, без лока.
  const naive = path.join(path.dirname(file), 'naive.mjs');
  const plain = path.join(path.dirname(file), 'plain.jsonl');
  fs.writeFileSync(plain, `${JSON.stringify({ title: 'ц', source: 'plan', state: 'live', text: '', log: [], tasks: [] })}\n`);
  fs.writeFileSync(naive, `
    import fs from 'node:fs';
    const file = process.argv[2];
    for (let i = 0; i < ${PER_WRITER}; i += 1) {
      const goals = fs.readFileSync(file, 'utf8').split('\\n').filter(Boolean).map(JSON.parse);
      goals[0].tasks = [...goals[0].tasks, { n: goals[0].tasks.length + 1, title: 'з' + process.argv[3] + '.' + i, where: [], body: '', state: 'open' }];
      const tmp = file + '.tmp.' + process.pid;
      fs.writeFileSync(tmp, goals.map((g) => JSON.stringify(g)).join('\\n') + '\\n');
      fs.renameSync(tmp, file);
    }
  `);
  await runWriters(naive, plain);

  const [raced] = registry.readRegistry(plain);
  assert.ok(raced.tasks.length < EXPECTED,
    `без лока часть записей теряется, осталось ${raced.tasks.length}`);
});
