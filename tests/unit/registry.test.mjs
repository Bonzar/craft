// Реестр одобренного: цели и задачи, в которые раскладывается всё, на что Влад
// дал ок. Тест держит поведение модуля, от которого зависят три хука-источника
// и сверка правки, — форму записи, замещение цели, закрытие, лог и глушилку.
//
// Расширение .mjs, а не .js: в каталоге тестов нет манифеста модулей, и .js
// читался бы как обычный скрипт, которому импорт недоступен.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';

const registry = await import('../../.claude/hooks/lib/registry.js');

// Песочницы кейсов сносятся одним разом в конце файла: их тут по одной на кейс,
// а временный каталог здесь же служит каталогом состояния хуков.
const sandboxes = [];
after(() => {
  for (const dir of sandboxes) fs.rmSync(dir, { recursive: true, force: true });
});

function tmpFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'registry-test-'));
  sandboxes.push(dir);
  return path.join(dir, 'approvals.jsonl');
}

// Приём кончается ПОЗЖЕ хода и возвращает сводку сессии в очередь хранения.
// Кейсу, который про хранение ничего не проверяет, это надо выключить и увести
// во временный каталог: без этого приём пишет в журнал ЖИВОЙ сессии (журнал
// резолвится по идентификатору сессии, а он у запускающего есть) и кладёт
// очередь в общий git-каталог НАСТОЯЩЕГО чекаута, откуда следующий Stop увезёт
// её в ветку metrics. Приём ПЛАНА очередь не заводит, но журнал и сводку правит
// так же, поэтому помощник нужен и ему. Кейсы, которые хранение как раз и
// проверяют, выставляют эти переменные сами и по-своему.
const storeOff = (dir) => ({
  CRAFT_METRICS_LOG: path.join(dir, 'metrics.jsonl'),
  METRICS_STORE_QUEUE: path.join(dir, 'queue.jsonl'),
  METRICS_STORE: 'off',
});

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

test('изменившаяся цель реплики замещает прежнюю вместе с задачами и логом', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal({ source: 'reply' }));
  registry.closeTasks(file, ['Ц1.1']);
  registry.appendLog(file, 0, 'задача Ц1.1 · registry.js · завела чтение');

  const revised = goal({ source: 'reply' });
  revised.tasks[0].body = 'форма записи, команды и глушилка';
  registry.upsertGoal(file, revised);

  const [saved] = registry.readRegistry(file);
  assert.equal(registry.readRegistry(file).length, 1);
  assert.equal(saved.tasks[0].state, 'open', 'ревизия отменяет прежнюю редакцию, а не продолжает её');
  assert.deepEqual(saved.log, []);
});

// Два разных плана про одну цель работы легко получают от модели один и тот же
// заголовок. Замещение стёрло бы открытые задачи первого — и гейт закрыл бы
// правки, которые Влад уже разрешил.
test('второй план с тем же заголовком заводит свою цель, а не затирает первую', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.closeTasks(file, ['Ц1.1']);
  registry.appendLog(file, 0, 'задача Ц1.1 · registry.js · завела чтение');

  const second = goal();
  second.tasks[0].body = 'другая работа под тем же именем';
  registry.upsertGoal(file, second);

  const saved = registry.readRegistry(file);
  assert.equal(saved.length, 2, 'план не замещает план');
  assert.equal(saved[0].tasks[0].state, 'closed', 'закрытое первого плана цело');
  assert.equal(saved[0].log.length, 1, 'и лог его цел');
  assert.equal(saved[1].tasks[0].state, 'open');
});

test('перепоказ того же плана цель не задваивает', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.upsertGoal(file, goal());
  assert.equal(registry.readRegistry(file).length, 1, 'то же содержание — та же цель');
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

test('закрытие держит тело в файле, но не печатает его', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  const done = registry.closeTasks(file, ['Ц1.1']);
  assert.deepEqual(done.closed, ['Ц1.1']);
  assert.deepEqual(done.unknown, []);

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.tasks[0].state, 'closed');
  assert.equal(saved.tasks[0].title, 'модуль реестра', 'имя нужно, чтобы отказ говорил правду');
  assert.match(saved.tasks[0].body, /форма записи и команды/, 'тело остаётся историей сессии');
  assert.ok(saved.tasks[0].where.length, 'адреса остаются историей сессии');

  const text = registry.render([saved]);
  assert.doesNotMatch(text, /форма записи и команды/, 'тело закрытой задачи в сверку не идёт');
});

test('неизвестный адрес не закрывает ничего и возвращается назад', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  const done = registry.closeTasks(file, ['Ц9.9', 'мусор', 'Ц1.7']);
  assert.deepEqual(done.closed, []);
  assert.deepEqual(done.unknown, ['Ц9.9', 'мусор', 'Ц1.7']);
  assert.equal(registry.readRegistry(file)[0].tasks[0].state, 'open');
});

test('цель со всеми закрытыми задачами не хоронится: текст и лог целы', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal({ text: 'зачем эта работа' }));
  registry.appendLog(file, 0, 'задача Ц1.1 · registry.js · завела чтение');
  registry.closeTasks(file, ['Ц1.1', 'Ц1.2']);

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.state, 'live', 'терминального состояния у цели нет');
  assert.equal(saved.text, 'зачем эта работа', 'текст цели остаётся');
  assert.deepEqual(saved.log, ['задача Ц1.1 · registry.js · завела чтение'], 'лог остаётся историей');
  assert.equal(saved.tasks.length, 2);

  const text = registry.render([saved]);
  assert.doesNotMatch(text, /работа закрыта/, 'пометки, глушившей приём, в тексте нет');
});

test('цель с закрытыми задачами принимает новую задачу и возвращается в работу', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.closeTasks(file, ['Ц1.1', 'Ц1.2']);
  registry.addTasks(file, 0, [{ title: 'третья задача', where: ['tools/registry.mjs'], body: 'тело' }]);

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.tasks.length, 3, 'работа под целью продолжается новой задачей');
  assert.equal(saved.tasks[2].state, 'open');
  assert.equal(saved.tasks[2].n, 3, 'номер продолжает нумерацию цели');
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

// Список работы — живая часть реестра, и нумерация в нём обязана остаться
// настоящей: по этим адресам закрывают, а сдвиг адресовал бы закрытие на чужую
// цель. Поле n у цели задаёт номер явно, минуя позицию в массиве.
test('рендер держит номер цели, заданный явно', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal({ title: 'первая' }));
  registry.upsertGoal(file, goal({ title: 'вторая', tasks: [{ title: 'её задача', where: [], body: 'тело' }] }));

  const [, second] = registry.readRegistry(file);
  const text = registry.render([{ ...second, n: 2 }], { bodies: false });
  assert.match(text, /^Ц2 «вторая»/m, 'цель осталась второй');
  assert.match(text, /задача Ц2\.1 «её задача»/, 'адрес задачи не съехал');
});

// Сквозной приём поверх ЗАКОНЧЕННОЙ работы. Это тот самый случай, на котором
// контур онемел: цель одна, все её задачи закрыты, и материал про продолжение
// той же работы (обновить описание PR, ответить ревьюеру) не ложился никуда —
// приём отклонял его, а сверка потом отказывала «работа уже закрыта».
//
// Кейс гоняет НАСТОЯЩИЙ приём, а не только ядро: между ответом модели и файлом
// стоит tools/registry-ingest.mjs, и запрет жил именно на этом пути.
test('приём вешает новую задачу на цель, вся работа под которой закрыта', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.closeTasks(file, ['Ц1.1', 'Ц1.2']);
  assert.ok(registry.readRegistry(file)[0].tasks.every((t) => t.state === 'closed'),
    'предусловие: под целью не осталось открытой работы');

  const material = path.join(path.dirname(file), 'material.txt');
  fs.writeFileSync(material, 'обнови описание PR под то, что реально сделано');

  const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
  execFileSync(process.execPath, [
    path.join(repo, 'tools', 'registry-ingest.mjs'), 'reply', material, file, 'проба',
  ], {
    stdio: 'ignore',
    env: {
      ...process.env,
      ...storeOff(path.dirname(file)),
      PLAN_CLASSIFIER_CMD: path.join(repo, 'tests', 'hooks', 'fixtures', 'mock-classifier.sh'),
      MOCK_CLASSIFIER_INGEST: JSON.stringify({
        add: [{ goal: 'Ц1', tasks: [{ title: 'работа продолжается', where: ['README.md'], anchor: '' }] }],
        close: [],
      }),
    },
  });

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.tasks.length, 3, 'материал лёг задачей под ту же цель');
  assert.equal(saved.tasks[2].title, 'работа продолжается');
  assert.equal(saved.tasks[2].state, 'open', 'цель снова в работе');
});

// Вид записи: работа или запрет. До него запрет лежал такой же записью, как
// рабочая цель, и сверка на каждой правке заново решала по смыслу текста, где
// тут запрет, — находила его в теле задачи и блокировала работу, которую это же
// тело описывает.
test('запрет ложится записью своего вида и без задач', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.upsertGoal(file, {
    title: 'никогда не создавай .ts',
    source: 'reply',
    kind: 'ban',
    text: 'никогда не создавай .ts',
    tasks: [{ title: 'задача, которой у запрета быть не должно' }],
  });

  const [work, ban] = registry.readRegistry(file);
  assert.equal(work.kind, 'work', 'обычная цель — работа');
  assert.equal(ban.kind, 'ban');
  assert.deepEqual(ban.tasks, [], 'у запрета задач не бывает: работы под ним нет');

  const text = registry.render(registry.readRegistry(file), { bodies: false });
  assert.match(text, /«никогда не создавай \.ts» — ЗАПРЕТ/, 'вид виден сверке в тексте');
  assert.match(text, /«# Юнит 1[^»]*» — работа/, 'у работы вид тоже назван');
});

// Снятие запрета: Влад передумал. Без него запрет жил вечно — приём умел
// добавлять записи и закрывать задачи, а у запрета задач нет, и убрать его было
// нечем. Строка остаётся в файле: на её позиции держится нумерация соседей.
test('снятый запрет уходит из текста для сверки, но остаётся в файле', () => {
  const file = tmpFile();
  registry.upsertGoal(file, { title: 'никогда не создавай .ts', source: 'reply', kind: 'ban' });
  registry.upsertGoal(file, goal());

  const done = registry.liftBans(file, ['Ц1']);
  assert.deepEqual(done.lifted, ['Ц1']);
  assert.deepEqual(done.unknown, []);

  const goals = registry.readRegistry(file);
  assert.equal(goals.length, 2, 'строка запрета остаётся — на ней держится нумерация');
  assert.equal(goals[0].state, 'lifted');

  const text = registry.render(goals, { bodies: false });
  assert.doesNotMatch(text, /не создавай \.ts/, 'снятый запрет сверке не показывается');
  assert.match(text, /Ц2 «# Юнит 1[^»]*» — работа/, 'номер соседа не съехал');
});

test('снять можно только живой запрет: работа и повтор уходят в неизвестные', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.upsertGoal(file, { title: 'не трогай прод', source: 'reply', kind: 'ban' });

  assert.deepEqual(registry.liftBans(file, ['Ц1']).unknown, ['Ц1'], 'работа запретом не бывает');
  assert.deepEqual(registry.liftBans(file, ['Ц2']).lifted, ['Ц2']);
  assert.deepEqual(registry.liftBans(file, ['Ц2']).unknown, ['Ц2'], 'снятый второй раз не снимается');
  assert.deepEqual(registry.liftBans(file, ['Ц9', 'мусор']).unknown, ['Ц9', 'мусор']);
});

// Переоткрытие задачи. Закрывает её агент по смыслу сделанного — значит он же в
// этом суждении ошибается: записал лог, счёл работу законченной, а тег не
// проставил. Без возврата ошибка кончалась тупиком: сверка отказывала «работа
// закрыта», и доделку приходилось разрешать заново, хотя цель одобрена.
test('закрытая задача возвращается в работу и снова видна в тексте', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  registry.closeTasks(file, ['Ц1.1']);

  const done = registry.reopenTasks(file, ['Ц1.1']);
  assert.deepEqual(done.reopened, ['Ц1.1']);
  assert.deepEqual(done.unknown, []);

  const [saved] = registry.readRegistry(file);
  assert.equal(saved.tasks[0].state, 'open');
  assert.match(saved.tasks[0].body, /форма записи и команды/, 'тело всё это время лежало на месте');

  const text = registry.render([saved]);
  assert.match(text, /задача Ц1\.1 «модуль реестра» — открыта/, 'сверка снова видит задачу открытой');
  assert.match(text, /форма записи и команды/, 'и её тело вернулось в текст');
});

test('возвращать нечего: открытая задача и неизвестный адрес уходят в неизвестные', () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  const done = registry.reopenTasks(file, ['Ц1.1', 'Ц9.9', 'мусор']);
  assert.deepEqual(done.reopened, []);
  assert.deepEqual(done.unknown, ['Ц1.1', 'Ц9.9', 'мусор']);
});

// Выбор цели, под которую ложится материал. Дефект, ради которого функция и
// появилась: второй план сессии садился задачами на цель первого — и юниты
// нового плана оказывались чужой работой, а сверка искала покрытие не там.
test('план на существующую цель не садится — ни на чужую, ни на план', () => {
  const goals = [
    { title: 'работа прошлого плана', source: 'plan', kind: 'work', tasks: [] },
    { title: 'реплика про то же самое', source: 'reply', kind: 'work', tasks: [] },
  ];
  assert.equal(registry.landingGoal(goals, 'Ц1', 'plan'), -1, 'даже цель прошлого плана — чужая граница работы');
  assert.equal(registry.landingGoal(goals, 'Ц2', 'plan'), -1, 'реплико-цель тем более');
});

test('реплика и кнопка садятся на цель любого источника', () => {
  const goals = [
    { title: 'работа плана', source: 'plan', kind: 'work', tasks: [] },
    { title: 'работа реплики', source: 'reply', kind: 'work', tasks: [] },
  ];
  assert.equal(registry.landingGoal(goals, 'Ц1', 'reply'), 0, 'реплика уточняет работу плана');
  assert.equal(registry.landingGoal(goals, 'Ц1', 'button'), 0, 'кнопка тоже');
  assert.equal(registry.landingGoal(goals, 'Ц2', 'reply'), 1);
});

test('негодный адрес цели приземления не даёт', () => {
  const goals = [{ title: 'работа плана', source: 'plan', kind: 'work', tasks: [] }];
  for (const ref of ['', 'Ц9', 'мусор', 'Ц0']) {
    assert.equal(registry.landingGoal(goals, ref, 'reply'), -1, `адрес «${ref}» цели не даёт`);
  }
});

test('совпавший заголовок у другого источника заводит вторую цель, а не затирает первую', () => {
  const file = tmpFile();
  registry.upsertGoal(file, { title: 'Журнал решений', source: 'reply', tasks: [{ title: 'завести журнал' }] });
  registry.closeTasks(file, ['Ц1.1']);
  registry.appendLog(file, 0, 'задача Ц1.1 · craft · завела страницу');

  registry.upsertGoal(file, { title: 'Журнал решений', source: 'plan', tasks: [{ title: 'наполнить журнал' }] });

  const saved = registry.readRegistry(file);
  assert.equal(saved.length, 2, 'цель опознаётся заголовком вместе с источником');
  assert.equal(saved[0].source, 'reply');
  assert.equal(saved[0].tasks[0].state, 'closed', 'прежняя цель цела');
  assert.equal(saved[0].log.length, 1, 'и лог её цел');
  assert.equal(saved[1].source, 'plan');
});

test('второй проход приёма находит цель, заведённую первым', () => {
  const before = [{ title: 'работа прошлого плана', source: 'plan', kind: 'work', tasks: [] }];
  const after = [...before, { title: 'работа этого плана', source: 'plan', kind: 'work', tasks: [] }];
  // ownFrom = 1: всё до неё лежало в реестре до приёма и для плана чужое.
  assert.equal(registry.landingGoal(after, 'Ц2', 'plan', 1), 1, 'своя цель этого же приёма');
  assert.equal(registry.landingGoal(after, 'Ц1', 'plan', 1), -1, 'цель прошлого плана по-прежнему чужая');
  assert.equal(registry.landingGoal(after, 'Ц2', 'plan'), -1, 'без границы своих целей нет');
});

// Ссылка внутри ОДНОГО ответа разбора: вторая запись адресует цель, заведённую
// первой записью того же ответа. Раньше это дозаводил второй проход приёма;
// проход теперь один, и реестр перечитывается на каждой записи — без этого
// вторая запись не находила цель первой и заводила третью.
test('приём видит цель, заведённую предыдущей записью того же ответа', () => {
  const file = tmpFile();
  const material = path.join(path.dirname(file), 'material.txt');
  fs.writeFileSync(material, 'план: сперва ядро, потом кейсы под него');

  const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
  execFileSync(process.execPath, [
    path.join(repo, 'tools', 'registry-ingest.mjs'), 'plan', material, file, 'проба',
  ], {
    stdio: 'ignore',
    env: {
      ...process.env,
      ...storeOff(path.dirname(file)),
      PLAN_CLASSIFIER_CMD: path.join(repo, 'tests', 'hooks', 'fixtures', 'mock-classifier.sh'),
      MOCK_CLASSIFIER_INGEST: JSON.stringify({
        add: [
          { goal_new: 'Работа плана', tasks: [{ title: 'ядро', where: ['lib/registry.js'], anchor: '' }] },
          { goal: 'Ц1', tasks: [{ title: 'кейсы под ядро', where: ['tests/'], anchor: '' }] },
        ],
        close: [],
      }),
    },
  });

  const goals = registry.readRegistry(file);
  assert.equal(goals.length, 1, 'вторая запись ответа села на цель первой, а не завела свою');
  assert.deepEqual(goals[0].tasks.map((t) => t.title), ['ядро', 'кейсы под ядро']);
});

// Два приёма одной сессии идут ПАРАЛЛЕЛЬНО: реплика уходит в фон, и ответ на
// кнопку следом за ней — тоже. На общем имени вида один приём подчищал за
// собой файл, который второй ещё не прочитал, и тот разбирал пустой реестр:
// заводил цель заново вместо того, чтобы сесть на существующую.
//
// Кейс подменяет КЛАССИФИКАТОР (не команду модели): ему видно имя вида и то,
// доживает ли файл до чтения. Медленный приём стартует первым и читает вид
// после того, как быстрый закончил и убрался.
test('вид реестра переживает параллельный приём', async () => {
  const file = tmpFile();
  registry.upsertGoal(file, goal());
  const dir = path.dirname(file);
  const material = path.join(dir, 'material.txt');
  fs.writeFileSync(material, 'продолжаем ту же работу');

  const seen = path.join(dir, 'seen.log');
  const stub = path.join(dir, 'stub-classifier.sh');
  fs.writeFileSync(stub, [
    '#!/usr/bin/env bash',
    'view="$2"',
    'sleep "${STUB_DELAY:-0}"',
    'if [[ -r "$view" ]]; then state=READABLE; else state=MISSING; fi',
    'printf "%s %s\\n" "$state" "$view" >> "$STUB_SEEN"',
    'printf \'{"add":[],"close":[]}\\n\'',
  ].join('\n'));

  const run = (delay) => new Promise((done) => {
    const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
    const child = spawn(process.execPath, [
      path.join(repo, 'tools', 'registry-ingest.mjs'), 'reply', material, file, `проба-${delay}`,
    ], {
      stdio: 'ignore',
      env: {
        ...process.env,
        ...storeOff(path.dirname(file)),
        PLAN_CLASSIFIER_BIN: stub,
        STUB_SEEN: seen,
        STUB_DELAY: String(delay),
      },
    });
    child.on('exit', done);
  });

  const slow = run(2);
  await new Promise((r) => { setTimeout(r, 200); });
  await run(0);
  await slow;

  const lines = fs.readFileSync(seen, 'utf8').trim().split('\n');
  assert.equal(lines.length, 2, 'оба приёма дошли до классификатора');
  const paths = lines.map((ln) => ln.split(' ')[1]);
  assert.notEqual(paths[0], paths[1], 'у каждого приёма своё имя вида');
  for (const ln of lines) {
    assert.match(ln, /^READABLE /, `вид дожил до чтения: ${ln}`);
  }
});

// Приём кончается ПОЗЖЕ последнего Stop сессии: работник хранения к тому времени
// увёз сводку и снял очередь, а вызов модели этого приёма попал в сводку уже
// после. Без возврата в очередь он не доехал бы никуда — следующего Stop у
// сессии может не быть.
test('приём возвращает пересобранную сводку в очередь хранения', () => {
  const file = tmpFile();
  const dir = path.dirname(file);
  const material = path.join(dir, 'material.txt');
  fs.writeFileSync(material, 'продолжаем ту же работу');

  const log = path.join(dir, 'metrics.jsonl');
  const queue = path.join(dir, 'queue.jsonl');
  fs.writeFileSync(`${log}.summary.json`, `${JSON.stringify({ sid: 'm-sid', turns: 2 })}\n`);

  const stub = path.join(dir, 'stub-classifier.sh');
  fs.writeFileSync(stub, ['#!/usr/bin/env bash', 'printf \'{"add":[],"close":[]}\\n\''].join('\n'));

  const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
  execFileSync(process.execPath, [
    path.join(repo, 'tools', 'registry-ingest.mjs'), 'reply', material, file, 'проба',
  ], {
    stdio: 'ignore',
    env: {
      ...process.env,
      PLAN_CLASSIFIER_BIN: stub,
      CLAUDE_CODE_SESSION_ID: 'm-sid',
      CRAFT_METRICS_LOG: log,
      METRICS_STORE_QUEUE: queue,
      METRICS_STORE: '',
    },
  });

  const rows = fs.readFileSync(queue, 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  assert.equal(rows.length, 1, 'сводка встала в очередь одной строкой');
  assert.equal(rows[0].sid, 'm-sid');
  assert.equal(rows[0].model_calls.count, 1, 'в очередь ушла сводка С вызовом модели этого приёма');
});

// Приём ПЛАНА идёт ВНУТРИ хода, синхронным вызовом из хука одобрения: возврат
// сводки в очередь там означал бы подпроцесс git и ожидание лока посреди хода,
// а следующий Stop положит сводку сам.
test('приём плана сводку в очередь не возвращает', () => {
  const file = tmpFile();
  const dir = path.dirname(file);
  const material = path.join(dir, 'material.txt');
  fs.writeFileSync(material, '## План\n\n- шаг\n');

  const log = path.join(dir, 'metrics.jsonl');
  const queue = path.join(dir, 'queue.jsonl');
  fs.writeFileSync(`${log}.summary.json`, `${JSON.stringify({ sid: 'm-sid', turns: 2 })}\n`);

  const stub = path.join(dir, 'stub-classifier.sh');
  fs.writeFileSync(stub, ['#!/usr/bin/env bash', 'printf \'{"add":[],"close":[]}\\n\''].join('\n'));

  const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
  execFileSync(process.execPath, [
    path.join(repo, 'tools', 'registry-ingest.mjs'), 'plan', material, file, 'проба',
  ], {
    stdio: 'ignore',
    env: {
      ...process.env,
      PLAN_CLASSIFIER_BIN: stub,
      CLAUDE_CODE_SESSION_ID: 'm-sid',
      CRAFT_METRICS_LOG: log,
      METRICS_STORE_QUEUE: queue,
      METRICS_STORE: '',
    },
  });

  assert.equal(fs.existsSync(queue), false, 'очередь на приёме плана не заводится');
});

// Выключатель хранения читает КРАЙ и передаёт его в постановку готовым
// значением. Без этого прогон кейсов копил бы очередь в общем git-каталоге
// НАСТОЯЩЕГО чекаута: сводки тестовых сессий уехали бы в ветку metrics.
test('приём с выключенным хранением очередь не заводит', () => {
  const file = tmpFile();
  const dir = path.dirname(file);
  const material = path.join(dir, 'material.txt');
  fs.writeFileSync(material, 'продолжаем ту же работу');

  const log = path.join(dir, 'metrics.jsonl');
  const queue = path.join(dir, 'queue.jsonl');
  fs.writeFileSync(`${log}.summary.json`, `${JSON.stringify({ sid: 'm-sid', turns: 2 })}\n`);

  const stub = path.join(dir, 'stub-classifier.sh');
  fs.writeFileSync(stub, ['#!/usr/bin/env bash', 'printf \'{"add":[],"close":[]}\\n\''].join('\n'));

  const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
  execFileSync(process.execPath, [
    path.join(repo, 'tools', 'registry-ingest.mjs'), 'reply', material, file, 'проба',
  ], {
    stdio: 'ignore',
    env: {
      ...process.env,
      PLAN_CLASSIFIER_BIN: stub,
      CLAUDE_CODE_SESSION_ID: 'm-sid',
      CRAFT_METRICS_LOG: log,
      METRICS_STORE_QUEUE: queue,
      METRICS_STORE: 'off',
    },
  });

  assert.equal(fs.existsSync(queue), false, 'при METRICS_STORE=off очередь не заводится');
});

// Материал приёма кладёт вызывающий во временный каталог и не убирает: приём
// отсоединён, ждать его некому. Значит убирает приём — и только СВОЙ каталог:
// временный каталог здесь же служит каталогом состояния хуков, и снести чужое
// там дороже, чем оставить своё.
test('приём убирает за собой каталог материала и не трогает чужой', () => {
  const file = tmpFile();
  const dir = path.dirname(file);
  const stub = path.join(dir, 'stub-classifier.sh');
  fs.writeFileSync(stub, ['#!/usr/bin/env bash', 'printf \'{"add":[],"close":[]}\\n\''].join('\n'));
  const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');

  const run = (material) => execFileSync(process.execPath, [
    path.join(repo, 'tools', 'registry-ingest.mjs'), 'reply', material, file, 'проба',
  ], {
    stdio: 'ignore',
    env: {
      ...process.env,
      ...storeOff(dir),
      PLAN_CLASSIFIER_BIN: stub,
      CLAUDE_CODE_SESSION_ID: 'm-sid',
    },
  });

  // Свой каталог — по маске вызывающего И под системным временным каталогом.
  const mine = fs.mkdtempSync(path.join(os.tmpdir(), 'registry-ingest-'));
  sandboxes.push(mine); // на случай, если приём упадёт раньше уборки
  const material = path.join(mine, 'material.txt');
  fs.writeFileSync(material, 'продолжаем ту же работу');
  run(material);
  assert.equal(fs.existsSync(mine), false, 'свой каталог материала убран');

  // Чужой по ИМЕНИ каталог остаётся: приём не сторож чужому временному файлу.
  const alien = path.join(dir, 'material.txt');
  fs.writeFileSync(alien, 'продолжаем ту же работу');
  run(alien);
  assert.equal(fs.existsSync(alien), true, 'чужой материал не тронут');

  // И чужой по МЕСТУ — тоже: имя совпадает с маской вызывающего, но каталог
  // лежит не под системным временным. Обе половины гварда проверяются, иначе
  // снятая проверка места прошла бы молча.
  const lookalike = path.join(dir, 'registry-ingest-подделка');
  fs.mkdirSync(lookalike);
  const inside = path.join(lookalike, 'material.txt');
  fs.writeFileSync(inside, 'продолжаем ту же работу');
  run(inside);
  assert.equal(fs.existsSync(lookalike), true, 'каталог не под системным временным не трогается');
});

// Приём идёт СЛЕДОМ за ходом, и вставать на лок очереди, который отсоединённый
// работник хранения держит всё время сети, ему нельзя: пять минут ожидания в
// этом месте — это пять минут, которые ждёт Влад. Не встали — пропуск виден
// строкой в журнале, а не тишиной.
test('занятый лок очереди не задерживает приём', () => {
  const file = tmpFile();
  const dir = path.dirname(file);
  const material = path.join(dir, 'material.txt');
  fs.writeFileSync(material, 'продолжаем ту же работу');

  const log = path.join(dir, 'metrics.jsonl');
  const queue = path.join(dir, 'queue.jsonl');
  fs.writeFileSync(`${log}.summary.json`, `${JSON.stringify({ sid: 'm-sid', turns: 2 })}\n`);
  // Лок держит ЖИВОЙ чужой процесс: отобрать его нельзя, дождаться — тоже.
  const holder = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 60000)'], { stdio: 'ignore' });
  fs.mkdirSync(`${queue}.lock`);
  fs.writeFileSync(path.join(`${queue}.lock`, 'owner'), String(holder.pid));

  const stub = path.join(dir, 'stub-classifier.sh');
  fs.writeFileSync(stub, ['#!/usr/bin/env bash', 'printf \'{"add":[],"close":[]}\\n\''].join('\n'));

  const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
  const started = Date.now();
  execFileSync(process.execPath, [
    path.join(repo, 'tools', 'registry-ingest.mjs'), 'reply', material, file, 'проба',
  ], {
    stdio: 'ignore',
    env: {
      ...process.env,
      PLAN_CLASSIFIER_BIN: stub,
      CLAUDE_CODE_SESSION_ID: 'm-sid',
      CRAFT_METRICS_LOG: log,
      METRICS_STORE_QUEUE: queue,
      METRICS_STORE: '',
    },
  });
  const spent = Date.now() - started;
  holder.kill('SIGKILL');

  assert.ok(spent < 10000, `приём не встал на чужой лок: ${spent} мс`);
  const lines = fs.readFileSync(log, 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  const skip = lines.find((r) => r.kind === 'skip' && r.what === 'queue');
  assert.ok(skip, 'пропуск постановки в очередь назван в журнале');
});
