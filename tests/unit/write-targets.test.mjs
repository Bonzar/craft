// Признаки вызова: трогает ли он мир и чем именно. Имена инструментов живут в
// адаптере харнеса, разбор команды — в адаптере шелла, решение — в общей части;
// кейсы гоняют их в связке, как это делает обёртка.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const tools = await import('../../.claude/hooks/lib/write-targets.js');
const claude = await import('../../.claude/hooks/lib/tool-flags-claude.js');
const bash = await import('../../.claude/hooks/lib/write-targets-bash.js');
const git = await import('../../.claude/hooks/lib/write-targets-git.js');
const repo = await import('../../.claude/hooks/lib/repo-git.js');
const hash = await import('../../.claude/hooks/lib/call-hash.js');
const metrics = await import('../../.claude/hooks/lib/metrics.js');

// Та же связка, что собирает обёртка (universal-metrics.js): область вызова и
// его форма от адаптера харнеса, разбор команды от адаптеров шелла и git.
const ADAPTERS = {
  commandWrites: (text) => ({ mutates: git.gitMutates(text), targets: bash.commandTargets(text) }),
  ignored: repo.isIgnored,
};
const mutation = (tool, input = {}) => tools.mutationOf(
  claude.toolScope(tool, input), claude.callShape(tool, input), ADAPTERS,
);
const mutates = (tool, input = {}) => mutation(tool, input).mutates === true;


test('читающие команды гита мутацией не считаются', () => {
  for (const cmd of ['git diff --merge-base main', 'git log --oneline -- lib/tag.js',
    'git show HEAD:src/reset.js', 'git stash list', 'git stash show', 'git tag',
    'git tag --list', "git tag -l 'v*'", 'git branch', 'git branch -a', 'git branch -r',
    'git remote', 'git remote -v', 'git remote show origin', 'git worktree list',
    'git status']) {
    assert.equal(mutates('Bash', { command: cmd }), false, cmd);
  }
  for (const cmd of ['git commit -m x', 'git -C /repo push origin main', 'git stash push -m wip',
    'git stash', 'git tag v1', 'git checkout -b feature',
    // Правящие формы тех же подкоманд и сетевые вызовы: они пишут ссылки и
    // объекты в локальный репозиторий, и ход с ними ходом без изменений не был.
    'git fetch origin', 'git pull', 'git branch feature', 'git branch -d old',
    'git remote add origin https://example.invalid/r.git', 'git worktree add /tmp/w']) {
    assert.equal(mutates('Bash', { command: cmd }), true, cmd);
  }
});

// В цепочке смотрятся ВСЕ вызовы: по одному первому «git status && git commit»
// читался бы как ход без единого изменения, а слово в кавычках — как правка.
test('правка видна в любом месте цепочки, а слово в кавычках правкой не считается', () => {
  assert.equal(mutates('Bash', { command: 'git status && git commit -m x' }), true);
  assert.equal(mutates('Bash', { command: 'git log --oneline | head; git tag v1' }), true);
  assert.equal(mutates('Bash', { command: "printf 'git commit -m x'" }), false);
  assert.equal(mutates('Bash', { command: 'echo git push' }), false);
});

test('работа через подагента считается прогрессом, чтение — нет', () => {
  assert.equal(mutates('Task', { subagent_type: 'general-purpose' }), true);
  assert.equal(mutates('Task', { subagent_type: 'Explore' }), false, 'разведка мир не трогает');
  assert.equal(mutates('mcp__Claude_Code_Remote__subscribe_pr_activity', {}), false,
    'обслуживание своего хода — не правка мира, как и у гейта');
});

test('прогресс: удавшаяся мутация через Bash или чужой MCP-инструмент записи', () => {
  assert.equal(mutates('Bash', { command: 'echo x >> README.md' }), true);
  assert.equal(mutates('Bash', { command: 'git commit -m x' }), true);
  assert.equal(mutates('Bash', { command: 'ls -la' }), false);
  assert.equal(mutates('Bash', { command: 'echo x > /tmp/scratch.txt' }), false, 'эфемерная цель — не мутация');
  assert.equal(mutates('mcp__github__create_pull_request', {}), true);
  assert.equal(mutates('mcp__github__list_pull_requests', {}), false);
  assert.equal(mutates('Edit', { file_path: '/home/user/craft/README.md' }), true);
  assert.equal(mutates('Edit', { file_path: '/tmp/scratch.txt' }), false,
    'правка эфемерной цели прогрессом не считается — как и запись в неё через шелл');
  assert.equal(mutates('Edit', {}), false, 'правка без цели мир не меняет');
  assert.equal(mutates('Read', {}), false);
  // «Менял ли мир» стоит на записи СОСТОЯВШЕГОСЯ вызова: до решения этого не
  // спрашивают вовсе — наблюдатель зовётся до решателей, и разбор целей записи
  // стоил бы запусков git за вызов, который ещё могут запретить.
  assert.equal(metrics.isProgress({ tool: 'Bash' }, { error: false, mutates: true }), true);
  assert.equal(metrics.isProgress({ tool: 'Bash' }, { error: true, mutates: true }), false);
  assert.equal(metrics.isProgress({ tool: 'Bash' }, { error: false, mutates: false }), false);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: true }, { error: false }), false,
    'признак на записи ДО вызова прогрессом больше не считается: вызов мог не состояться');
});

// Без адаптера интерпретатора общая часть НЕ говорит «мир не менялся»: своего
// разбора команд у неё нет, и молчаливое «false» соврало бы про каждый ход,
// который работал шеллом. Отсутствие возможности называется явно.
test('команда без адаптера даёт unsupported, а не тихое «не менял»', () => {
  const scope = claude.toolScope('Bash', { command: 'echo x >> README.md' });
  const call = claude.callShape('Bash', { command: 'echo x >> README.md' });
  assert.deepEqual(tools.mutationOf(scope, call, {}),
    { status: 'unsupported', capability: 'write-targets' });
  assert.deepEqual(tools.mutationOf(scope, call, ADAPTERS), { status: 'ok', mutates: true });
});

// Область вызова приходит ДАННЫМИ: общая часть про имена инструментов не знает
// вовсе, и одного адаптера довольно, чтобы подключить другой харнес.
test('область вызова решает вопрос «трогает ли мир» без имён инструментов', () => {
  assert.equal(tools.touchesWorld({ reads: true }), false);
  assert.equal(tools.touchesWorld({ session: true }), false);
  assert.equal(tools.touchesWorld({}), true);
  assert.deepEqual(claude.toolScope('Read', {}), { reads: true });
  assert.deepEqual(claude.toolScope('ExitPlanMode', {}), { session: true });
  assert.deepEqual(claude.toolScope('Task', { subagent_type: 'Explore' }), { reads: true });
  assert.deepEqual(claude.toolScope('Bash', {}), {});
});

// Игнорируемый путь эфемерен, но спрашивают об этом АДАПТЕР: без него общая
// часть не смеет считать путь игнорируемым — иначе мимо гейта прошла бы любая
// правка на цели, про которую слой ничего не знает.
test('игнорируемое репозиторием эфемерно, и вопрос задаёт адаптер', () => {
  const ignored = (fp) => fp.endsWith('.log');
  assert.equal(tools.ignoredEphemeral('build/out.log', ignored), true);
  assert.equal(tools.ignoredEphemeral('build/out.log', undefined), false,
    'без адаптера ответ «нет», а не «да»');
  assert.equal(tools.ignoredEphemeral('.claude/hooks/x.log', ignored), false,
    'внутри .claude/ игнор не оправдание');
});

test('служебные поля входа отсеивает адаптер харнеса, а не хеш вызова', () => {
  // Та же связка, что в обёртке: `callHash(инструмент, semanticInput(...))`.
  const callOf = (tool, input) => hash.callHash(tool, claude.semanticInput(tool, input));
  assert.equal(
    callOf('Bash', { command: 'git push', description: 'Push branch', timeout: 120000 }),
    callOf('Bash', { command: 'git push', description: 'Push the branch to origin' }),
    'переписанное описание не должно делать повтор другим вызовом',
  );
  assert.notEqual(callOf('Bash', { command: 'git push' }), callOf('Bash', { command: 'git status' }));
  assert.equal(
    callOf('KillShell', { shell_id: '1' }),
    callOf('KillShell', { shell_id: '2' }),
    'номер фонового запуска смыслом вызова не является',
  );
  // А чтение чужого вывода — является: у него идентификатор говорит, ЧЕЙ вывод.
  // Это сторож НА БУДУЩЕЕ, а не проба на снятую запись: прежняя запись отсеивала
  // у этого вызова `shell_id`, которого у него не бывает, и `bash_id` доходил до
  // хеша и тогда. Красным строка станет, если кто-нибудь заведёт `bash_id`
  // служебным полем.
  assert.notEqual(callOf('BashOutput', { bash_id: 'a' }), callOf('BashOutput', { bash_id: 'b' }));
  // Список служебных полей — ПО ИНСТРУМЕНТУ: у правки `description` нет вовсе, и
  // общий список отсеивал бы поле там, где оно могло быть смыслом.
  assert.notEqual(
    callOf('Edit', { file_path: 'a', description: 'x' }),
    callOf('Edit', { file_path: 'a', description: 'y' }),
  );
  assert.deepEqual(claude.semanticInput('Edit', { file_path: 'a' }), { file_path: 'a' });
  assert.deepEqual(claude.semanticInput('Bash', undefined), {});
});

// Цели записи и признак «менял ли» обязаны идти ОТ ОДНОЙ политики эфемерности:
// разъехавшись, они дали бы вызов, помеченный записью, с пустым списком целей —
// и это было бы видно только на живом прогоне.
test('цели записи чистятся тем же правилом, каким считается «менял ли вызов мир»', () => {
  const shape = (tool, input) => claude.callShape(tool, input);
  const mutates = (tool, input) => tools.mutationOf(claude.toolScope(tool, input), shape(tool, input), ADAPTERS);

  assert.deepEqual(tools.durableTargets(shape('Write', { file_path: '/repo/README.md' }), ADAPTERS),
    ['/repo/README.md']);
  // Эфемерная правка: и «менял» ложно, и целей нет. Проверяются ОБА ответа
  // разом — одна половина, зелёная в одиночку, ничего не значит.
  assert.equal(mutates('Write', { file_path: '/tmp/x' }).mutates, false);
  assert.deepEqual(tools.durableTargets(shape('Write', { file_path: '/tmp/x' }), ADAPTERS), []);

  const cmd = { command: 'printf x > /repo/out.txt' };
  assert.equal(mutates('Bash', cmd).mutates, true);
  assert.deepEqual(tools.durableTargets(shape('Bash', cmd), ADAPTERS), ['/repo/out.txt']);

  // Правка репозитория без перенаправления: «менял» истинно, а целей нет вовсе.
  // Значит по длине списка про запись судить нельзя.
  const commit = { command: 'git commit -m x' };
  assert.equal(mutates('Bash', commit).mutates, true);
  assert.deepEqual(tools.durableTargets(shape('Bash', commit), ADAPTERS), []);

  // Эфемерность чистится и в КОМАНДНОЙ ветке, а не только у правки: эта ветка
  // отдельная, и без своего кейса снятый в ней фильтр проходил молча.
  assert.deepEqual(
    tools.durableTargets(shape('Bash', { command: 'printf x > /tmp/черновик' }), ADAPTERS), [],
  );
  // Смесь долговечной и эфемерной цели: остаётся только долговечная.
  assert.deepEqual(
    tools.durableTargets(
      shape('Bash', { command: 'printf x > /repo/out.txt && printf y > /tmp/черновик' }), ADAPTERS,
    ),
    ['/repo/out.txt'],
  );
  // Нет адаптера команды — целей нет и назвать их нечем; имя недостающего
  // называет сам вопрос «менял ли».
  assert.deepEqual(tools.durableTargets(shape('Bash', cmd), {}), []);
  assert.equal(tools.mutationOf(claude.toolScope('Bash', cmd), shape('Bash', cmd), {}).capability,
    'write-targets');
});

// Цели ЧТЕНИЯ у команды. Гоняется НАСТОЯЩИЙ адаптер интерпретатора, а не
// заглушка: у кейсов общей части адаптер подставной, и вся эта сотня строк
// (списки команд, ключи со значением, перенаправления, обёртки, подстановка,
// резолв каталога) ими не трогалась вовсе.
//
// Порядок утверждений здесь не случаен: сперва ЛОЖНЫЕ цели, потом настоящие.
// Асимметрия жёсткая — пропущенная цель стоит агенту лишнего чтения, а ложная
// РАЗРЕШАЕТ гварду править то, чего никто не читал.
test('цели чтения: ложных нет', () => {
  const targets = (cmd) => bash.commandReads(cmd).targets;
  // Закавыченный образец — ОДИН токен. По словам обезвреженного текста он
  // рассыпался, и `README.md` из образца становился «прочитанным файлом».
  assert.deepEqual(targets('grep -rn "см. README.md" .'), []);
  assert.deepEqual(targets('cd /repo && grep -rn "см. README.md" .'), []);
  // Значение ключа не занимает слот образца — иначе в цели уезжает сам образец.
  assert.deepEqual(targets('grep -A 3 package.json src/index.js'), ['src/index.js']);
  assert.deepEqual(targets('head -c 100 /repo/f.js'), ['/repo/f.js']);
  // Каталог обхода прочитанным файлом не является: какие файлы под ним прочли,
  // назвать нечем, и выдать каталог за файл значило бы соврать.
  assert.deepEqual(targets('grep -rn образец /repo/src/'), []);
  // Содержимое подстановки — операнды ЧУЖОЙ команды.
  assert.deepEqual(targets('cat $(ls /repo/src)'), []);
  // `cd` СЛОВОМ команды каталогом перехода не является: приняв его за переход,
  // разбор резолвит им ОСТАТОК цепочки и выдаёт путь, которого никто не открывал.
  assert.deepEqual(targets('grep -n cd /repo/a.js && cat b.js'), ['/repo/a.js', 'b.js']);
  // Маркер heredoc и дескрипторы целями не становятся.
  assert.deepEqual(targets("cat <<'EOF'"), []);
  assert.deepEqual(targets('cat /repo/f.js > /dev/null 2>&1'), ['/repo/f.js']);
});

test('цели чтения: настоящие называются', () => {
  const targets = (cmd) => bash.commandReads(cmd).targets;
  assert.deepEqual(targets('cat /repo/a.js /repo/b.js'), ['/repo/a.js', '/repo/b.js']);
  assert.deepEqual(targets('sed -n 1,5p /repo/a.js'), ['/repo/a.js']);
  assert.deepEqual(targets('grep образец /repo/a.js'), ['/repo/a.js']);
  // Ключ, сам несущий образец: следующий операнд — уже файл, съедать его нельзя.
  assert.deepEqual(targets('grep -e A -e B /repo/a.js'), ['/repo/a.js']);
  // Путь с пробелом уцелел целиком — ровно потому, что разбор идёт токенами.
  assert.deepEqual(targets('cat "мой файл.txt"'), ['мой файл.txt']);
  assert.deepEqual(targets('wc -l < /repo/a.js'), ['/repo/a.js']);
  assert.deepEqual(targets('LC_ALL=C cat /repo/a.js'), ['/repo/a.js']);
  assert.deepEqual(targets('timeout 5 cat /repo/a.js'), ['/repo/a.js']);
  assert.deepEqual(targets('cat a.js & cat b.js'), ['a.js', 'b.js']);
  // Резолв каталогом, действующим В ЭТОМ МЕСТЕ цепочки.
  assert.deepEqual(targets('cd /repo/lib && sed -n 1,5p a.js'), ['/repo/lib/a.js']);
});

// «Не доказано» и «доказано, что не чтение» — РАЗНЫЕ ответы: по первому журнал
// обязан сказать имя, по второму имеет право промолчать.
test('цели чтения: разобранность отделена от чтения', () => {
  // Перенаправление в файл — ИЗВЕСТНАЯ запись, поэтому разбор про неё всё знает.
  assert.deepEqual(bash.commandReads('printf x > /repo/o.txt'),
    { reads: false, mutates: true, targets: [] });
  assert.equal(bash.commandReads('rm -rf /repo/x').mutates, true, 'про удаление разбор всё знает');
  assert.equal(bash.commandReads('node сборка.js').mutates, false, 'а про чужой запуск — ничего');
  assert.deepEqual(bash.commandReads(''), { reads: false, mutates: false, targets: [] });
  assert.equal(bash.commandReads('pwd').reads, true, 'читающая команда без файлов — всё равно чтение');
  assert.deepEqual(bash.commandReads('pwd').targets, []);
});

// Ложные цели, найденные третьим кругом ревью. Каждая — «разрешение править
// непрочитанное», то есть худший из возможных дефектов этого куска.
test('цели чтения: каталог и нераскрытая переменная целями не становятся', () => {
  const targets = (cmd) => bash.commandReads(cmd).targets;
  // Каталог рекурсивного обхода. Хвостовой косой чертой обычная его форма себя
  // не выдаёт, поэтому судить приходится по КЛЮЧУ рекурсии.
  assert.deepEqual(targets('grep -rn TODO /repo/lib'), []);
  assert.deepEqual(targets('grep -Rn TODO /repo/lib'), []);
  assert.deepEqual(targets('grep --recursive TODO /repo/lib'), []);
  // Поиск, рекурсивный ПО УМОЛЧАНИЮ: у него операнд неотличим никаким флагом.
  assert.deepEqual(targets('rg foo /repo/lib'), []);
  // А у потокового редактора `-r` — это расширенные регулярки, и общее правило
  // зря лишало бы его целей.
  assert.deepEqual(targets('sed -r s/a/b/ /repo/a.js'), ['/repo/a.js']);

  // Нераскрытая переменная: токенизатор подставляет её пустотой, и путь
  // получался ВЫДУМАННЫЙ — несуществующий вместо настоящего.
  assert.deepEqual(targets('cat $HOME/секрет.md'), []);
  assert.deepEqual(targets('cat ${DIR}/x.js'), []);
  assert.deepEqual(targets('grep foo "$HOME/a.js"'), []);
  assert.deepEqual(targets('cat < $HOME/x.js'), []);
  // Литерал в одинарных кавычках переменной не является и целей не отменяет.
  assert.deepEqual(targets("grep -n 'literal $HOME' /repo/a.js"), ['/repo/a.js']);
});

// Формы, в которых разбор ВЫДУМЫВАЛ прочитанный файл. Каждая — «разрешение
// править непрочитанное», и каждую нашли отдельным кругом ревью.
test('цели чтения: тело heredoc, переход каталога и правка на месте', () => {
  const targets = (cmd) => bash.commandReads(cmd).targets;
  // Тело heredoc словами команды не является, а токенизатор их не разделяет:
  // упомянутый в тексте файл становился прочитанным. Агент печатает через
  // heredoc постоянно.
  assert.deepEqual(targets('cat <<EOF\nсмотри README.md подробнее\nEOF'), []);
  assert.deepEqual(targets("cat <<'MSG'\nfix lib/journal.js first\nMSG"), []);
  assert.deepEqual(targets('grep foo <<< "text with a.js"'), []);
  assert.deepEqual(targets('diff <(echo config.yaml) other.txt'), []);

  // Неабсолютный переход: каталог сменился, а куда — неизвестно, и держаться за
  // прежний значит приклеивать его к чужим путям.
  assert.deepEqual(targets('cd /repo && cd sub && cat a.js'), ['a.js']);
  // Подоболочка меняет каталог только внутри себя.
  assert.deepEqual(targets('(cd /tmp) && cat a.js'), ['a.js']);
  // А обычный переход по-прежнему резолвит.
  assert.deepEqual(targets('cd /repo/lib && sed -n 1,5p a.js'), ['/repo/lib/a.js']);

  // Правка на месте с суффиксом ключа — не чтение, и разбор теперь ЗНАЕТ, что она
  // меняет состояние: раньше он сверял запрещённые ключи точным равенством и эту
  // форму пропускал, а на нём стоит отказ гварда якоря сессии.
  assert.deepEqual(bash.commandReads('sed -i.bak s/a/b/ /repo/README.md'),
    { reads: false, mutates: true, targets: [] });
  assert.deepEqual(bash.commandReads('sed --in-place=.bak s/a/b/ /repo/README.md'),
    { reads: false, mutates: true, targets: [] });
  // А `sed -n` остаётся чтением.
  assert.equal(bash.commandReads('sed -n 1,5p /repo/a.js').reads, true);

  // Обход каталогов сравнением: каталог прочитанным файлом не является.
  assert.deepEqual(targets('diff -r lib/old lib/new'), []);
  // Ключи, у которых значение крепится только через `=`, чужого слова не едят.
  assert.deepEqual(targets('grep --color foo a.js'), ['a.js']);
  assert.deepEqual(targets('jq --tab . data.json'), ['data.json']);
});

// Перевод строки разделяет команды так же, как `;`, но токенизатор его НЕ
// ВЫДАЁТ — для него это пробел. Многострочная команда оттого схлопывалась в одну:
// имя бралось из первой строки, а слова остальных становились её операндами.
// Ущерб тройной — правка на месте доказывалась ЧТЕНИЕМ переписанного файла,
// гвард якоря сессии пропускал многострочный `rm -rf`, и переписанный файл
// уезжал в журнал прочитанным, то есть открытым для правки.
test('перевод строки разделяет команды', () => {
  const mutating = (cmd) => bash.classifyCommand(cmd).readOnly === false;
  assert.ok(mutating('cat a.js\nrm -rf /repo/x'), 'удаление на второй строке');
  assert.ok(mutating('cat a.js\nsed -i s/a/b/ /repo/README.md'), 'правка на месте');
  assert.ok(mutating('head -5 a.txt\ncp /repo/src.js /repo/dst.js'), 'копирование');
  assert.equal(bash.classifyCommand('cat a.js\nrm -rf /repo/x').offender, 'rm', 'виновник назван');
  assert.deepEqual(bash.commandReads('cat a.js\nsed -i s/a/b/ /repo/README.md'),
    { reads: false, mutates: true, targets: [] });

  // Операнды второй строки не уезжают в файловый слот ПЕРВОЙ: образец остаётся
  // образцом, а не становится прочитанным файлом.
  assert.deepEqual(bash.commandReads('cat a.js\ngrep README.md b.js').targets, ['a.js', 'b.js']);
  // Раз строка — звено цепочки, переход каталога переносится на следующую.
  assert.deepEqual(bash.commandReads('cd /repo\ncat a.js').targets, ['/repo/a.js']);

  // И обратная сторона: ложного отказа быть не должно. Тело heredoc — печатаемый
  // ТЕКСТ, а не команды, даже когда в нём стоят слова вроде `rm -rf`.
  assert.equal(bash.classifyCommand('cat <<EOF\nrm -rf /x\nEOF').readOnly, true);
  assert.equal(bash.classifyCommand("cat <<'EOF'\ngit push --force\nEOF").readOnly, true);
  // Многострочная строка в кавычках — один аргумент, рвать её нельзя.
  assert.equal(bash.classifyCommand("echo 'первая\nвторая'").readOnly, true);
});

// Формы, где разбор называл прочитанным путь, которого НЕ СУЩЕСТВУЕТ: раскрыть
// их нечем, и записать как есть значило бы соврать про конкретный файл.
test('цели чтения: нераскрытое и неразвёрнутое путями не считаются', () => {
  const targets = (cmd) => bash.commandReads(cmd).targets;
  // Метку нераскрытой переменной отсеивали у операндов, а у САМОГО каталога
  // перехода — нет, и она уезжала внутрь пути вместе с байтами NUL.
  assert.deepEqual(targets('cd /repo/$SUB && cat a.js'), ['a.js']);
  // Символы подстановки и тильду токенизатор не раскрывает.
  assert.deepEqual(targets('cat file{1,2}.txt'), []);
  assert.deepEqual(targets('cat [ab].js'), []);
  assert.deepEqual(targets('cat ~/notes.md'), []);
  // Значение ключа у сравнения и просмотрщиков — не файл.
  assert.deepEqual(targets('diff -I foo.bar a.js b.js'), ['a.js', 'b.js']);
  assert.deepEqual(targets('less -p config.json a.js'), ['a.js']);
  assert.deepEqual(targets('bat --file-name a.js b.js'), ['b.js']);
  // Аргумент с плюсом — ключ в старой форме, а не файл.
  assert.deepEqual(targets('more +/foo a.js'), ['a.js']);
  // Переход каталога в звене ПАЙПА на соседа не влияет: это подоболочка.
  assert.deepEqual(targets('cd /tmp | cat a.js'), ['a.js']);
  // А через `&&` переход по-прежнему переносится, даже когда дальше есть пайп.
  assert.deepEqual(targets('cd /repo && cat a.js | grep x'), ['/repo/a.js']);
});

// Метаданные — не содержимое. Гвард «не правь того, чего не читал» на цели от
// `stat` разрешил бы правку файла, который агент не открывал.
test('чтение метаданных прочитанным файлом не делает', () => {
  assert.deepEqual(bash.commandReads('stat /repo/a.js').targets, []);
  assert.deepEqual(bash.commandReads('cat /repo/a.js').targets, ['/repo/a.js']);
});

// Обёртка запуска на стороне ЗАПИСИ. Тело обёртки — один закавыченный аргумент,
// а разбор целей кавычки вычёркивает: запись внутри неё выходила ПУСТЫМ списком,
// то есть «мутации нет» — проход мимо план-гейта и защиты конфигов. Скобки,
// группировку и запись из питона разбор держал; шелл через `-c` — нет, и это
// была не граница возможного, а несогласованность.
test('запись ВНУТРИ обёртки запуска цель даёт', () => {
  assert.deepEqual(bash.commandTargets('bash -c "cat > README.md"'), ['README.md']);
  assert.deepEqual(bash.commandTargets('sh -c "echo x > .claude/settings.json"'), ['.claude/settings.json']);
  assert.deepEqual(bash.commandTargets('zsh -c "echo x > a.txt"'), ['a.txt']);
  // Слипшийся кластер ключей — та же форма, что уже разбиралась у запрещённых
  // ключей: точное равенство пропускало `-lc` ровно так же, как `sed -i.bak`.
  assert.deepEqual(bash.commandTargets('bash -lc "cat > w.txt"'), ['w.txt']);
  // У ключа `-o` есть значение, и командой оно не является.
  assert.deepEqual(bash.commandTargets('bash -euo pipefail -c "cat > e.txt"'), ['e.txt']);
  // Цепочка внутри тела разбирается как обычная цепочка, с переходом каталога.
  assert.deepEqual(bash.commandTargets('bash -c "grep -n foo a.js && echo x > README.md"'), ['README.md']);
  assert.deepEqual(bash.commandTargets('bash -c "cd /tmp && cat > inner.txt"'), ['/tmp/inner.txt']);
});

// Спуск РОВНО на один уровень, и ни на шаг дальше: разобрать вложенную обёртку
// нечем, и назвать её цель значило бы выдумать. Пусто здесь — честный ответ, а
// разбор при этом молчанием не отделывается: команда остаётся неустановленной.
test('обёртка за обёрткой целей не даёт, и это не выдаётся за отсутствие правки', () => {
  assert.deepEqual(bash.commandTargets('bash -c \'bash -c "cat > deep.md"\''), []);
  assert.equal(bash.classifyCommand('bash -c \'bash -c "cat > deep.md"\'').readOnly, false);
});

// Обёрткой имя считается только ПЕРВЫМ словом звена — то же правило, что у `cd`,
// и по той же причине. Ложная цель на стороне записи это ложный отказ гварда.
test('слово обёртки не в начале звена целью записи не становится', () => {
  assert.deepEqual(bash.commandTargets('echo bash -c "x > y"'), []);
  // Ключ принадлежит СКРИПТУ, а не обёртке: до `-c` стоит неключевое слово. Тело
  // взято с настоящей записью — иначе проверка зеленела бы от того, что в
  // аргументе нет перенаправления, а не от правила о ведущих ключах.
  assert.deepEqual(bash.commandTargets('bash deploy.sh -c "echo x > README.md"'), []);
  // Нераскрытая переменная отбрасывает ОДНУ цель, а не всё тело: у
  // `bash -c "$CMD > README.md"` цель перенаправления буквальная, и выброшенное
  // целиком тело теряло настоящую запись. Проверка нагружена с обеих сторон —
  // на теле с настоящей целью и на теле, где нераскрыта сама цель.
  assert.deepEqual(bash.commandTargets('bash -c "$CMD > README.md"'), ['README.md']);
  assert.deepEqual(bash.commandTargets('bash -c "cat > $OUT"'), []);
});

// Стороны не симметричны: потерянная цель ЧТЕНИЯ — лишний отказ гварда,
// потерянная цель ЗАПИСИ — пропущенная правка. Поэтому обёртка разбирается
// только на стороне записи, а на стороне чтения пусто — и это намеренно.
test('на стороне ЧТЕНИЯ обёртка целей по-прежнему не даёт', () => {
  assert.deepEqual(bash.commandReads('bash -c "cat a.js"'), { reads: true, mutates: false, targets: [] });
});

// Одна цель, увиденная двумя разборами (снаружи по слову `tee`, внутри обёртки
// по нему же), для гвардов безразлична, а для ЛЕДЖЕРА это двойной счёт файла.
test('повтор цели в списке не остаётся', () => {
  assert.deepEqual(bash.commandTargets('cat a.js | bash -c "tee /repo/out.txt"'), ['/repo/out.txt']);
});

// Список файловых операндов обещает ровно то, что делает: цели спрашиваются
// только у команды, доказанно читающей, и имя, которого нет в словаре
// читаемости, не дало бы цели ни разу. Такими стояли `hexdump`, `shasum` и
// `sha1sum` — список врал своему читателю.
test('каждое имя в списке файловых операндов ДОСТИЖИМО', () => {
  // Список берётся ИЗ САМОГО РАЗБОРА, а не переписывается сюда: своя копия
  // зеленела бы и с вернувшимся мёртвым именем — проверка стояла бы не там,
  // куда смотрит.
  const pair = new Set(['diff', 'cmp']);
  assert.ok(bash.FILE_OPERANDS.size > 10);
  for (const name of bash.FILE_OPERANDS) {
    const cmd = pair.has(name) ? `${name} /repo/a.js /repo/b.js` : `${name} /repo/a.js`;
    const expected = pair.has(name) ? ['/repo/a.js', '/repo/b.js'] : ['/repo/a.js'];
    assert.deepEqual(bash.commandReads(cmd).targets, expected, name);
  }
});

// Что считать обёрткой, спрашивают ДВОЕ: доказательство читаемости (ему надо
// судить тело) и цели записи (им надо в тело спуститься). Снятие теперь одно на
// обоих, но словарь имён общий, и разъедься ответы — форма, которую один считает
// обёрткой, у другого осталась бы неразобранной. Проверяется поэтому КАЖДОЕ имя
// словаря с обеих сторон: без этой пары список можно было вынуть из одной
// стороны, подставив свой, и обе проверки остались бы зелёными — `dash`, `ksh` и
// `fish` не держал никто.
test('список обёрток у доказательства и у целей записи ОДИН', async () => {
  const rulesPath = new URL('../../.claude/hooks/lib/vendor/read-only-rules.json', import.meta.url);
  const { readFileSync } = await import('node:fs');
  const cfg = JSON.parse(readFileSync(rulesPath, 'utf8'));
  for (const name of cfg.shellWrappers) {
    assert.equal(bash.classifyCommand(`${name} -c "cat a.js"`).readOnly, true, name);
    assert.equal(bash.classifyCommand(`${name} -c "rm a.js"`).readOnly, false, name);
    assert.deepEqual(bash.commandTargets(`${name} -c "cat > README.md"`), ['README.md'], name);
  }
  // Имя, заданное путём и в другом регистре, обёрткой считают обе стороны.
  assert.equal(bash.classifyCommand('/bin/bash -c "rm a.js"').readOnly, false);
  assert.deepEqual(bash.commandTargets('/bin/bash -c "cat > README.md"'), ['README.md']);
  assert.deepEqual(bash.commandTargets('BASH -c "cat > README.md"'), ['README.md']);
  // И наоборот: имя не из списка обёрткой не считает ни одна.
  assert.deepEqual(bash.commandTargets('perl -c "cat > README.md"'), []);
});

// Звенья и строки у разбора обёртки — те же, что у доказательства читаемости.
// Свой проход по токенам ошибался дважды, и оба раза это был тихий пропуск
// правки: перевод строки токенизатор оператором не выдаёт, а `do`, `then` и `{`
// занимают слот начала звена.
test('обёртка видна в любом месте, где начинается команда', () => {
  const t = (cmd) => bash.commandTargets(cmd);
  assert.deepEqual(t('cat a.js\nbash -c "cat > README.md"'), ['README.md']);
  assert.deepEqual(t('for f in a b; do bash -c "cat > README.md"; done'), ['README.md']);
  assert.deepEqual(t('if true; then bash -c "cat > README.md"; fi'), ['README.md']);
  assert.deepEqual(t('{ bash -c "cat > README.md"; }'), ['README.md']);
  assert.deepEqual(t('(bash -c "cat > README.md")'), ['README.md']);
  assert.deepEqual(t('grep -n foo a.js && bash -c "cat > README.md"'), ['README.md']);
});

// Запускающая обёртка снимается ТЕМ ЖЕ словарём, что у доказательства
// читаемости (`dropWrappers`): иначе разбор записи знал бы про неё меньше, чем
// разбор чтения, — та самая несогласованность, ради которой всё это делается.
test('запускающая обёртка перед оболочкой цель не прячет', () => {
  assert.deepEqual(bash.commandTargets('timeout 5 bash -c "cat > README.md"'), ['README.md']);
  assert.deepEqual(bash.commandTargets('nohup bash -c "cat > README.md"'), ['README.md']);
  assert.deepEqual(bash.commandTargets('OUT=1 bash -c "cat > README.md"'), ['README.md']);
});

// Ключ обёртки со СВОИМ значением обрывал поиск `-c`, и запись проходила молча.
// Формы названы поимённо по грамматике самой оболочки.
test('ключ обёртки со значением поиск -c не обрывает', () => {
  assert.deepEqual(bash.commandTargets('bash -O extglob -c "cat > README.md"'), ['README.md']);
  assert.deepEqual(bash.commandTargets('bash --rcfile /tmp/rc -c "cat > README.md"'), ['README.md']);
  assert.deepEqual(bash.commandTargets('bash -euo pipefail -c "cat > README.md"'), ['README.md']);
});

// Переход каталога ВНУТРИ вложенной конструкции. Фигурные скобки исполняются в
// ТЕКУЩЕЙ оболочке — их `cd` действует и дальше по строке, — а подоболочка нет,
// и отличить одно от другого по дереву нечем. Ошибки этих двух сторон не равны:
// удержанный чужой каталог даёт АБСОЛЮТНЫЙ путь, который легко оказывается
// временным, и правка репозитория прошла бы мимо план-гейта и гварда якоря.
// Поэтому вложенный переход означает «каталог неизвестен», а не «каталог
// прежний»: цель остаётся относительной, то есть скорее долговечной.
// Порядок утверждений: содержимое подстановки ложится в список ПЕРЕД своим
// вызовом, и сосед «по индексу» отдал бы разделитель чужого утверждения. Пайп
// перестал бы разрывать переход каталога, и прочитанным назывался бы файл,
// которого никто не открывал.
test('подстановка между звеньями не отменяет правило пайпа', () => {
  const targets = (cmd) => bash.commandReads(cmd).targets;
  assert.deepEqual(targets('cd /tmp | cat a.js'), ['a.js']);
  assert.deepEqual(targets('cd /tmp | cat $(echo x) a.js'), ['a.js']);
  // А через `&&` переход по-прежнему переносится, в том числе с подстановкой.
  assert.deepEqual(targets('cd /repo && cat $(echo x) a.js'), ['/repo/a.js']);
});

// Оболочка открывает файл на запись не только через `>`. `<>` — на чтение и
// запись; `>&` с ИМЕНЕМ справа создаёт файл ровно как `>`, а с числом дублирует
// дескриптор. Прежний разбор искал стрелку по тексту и `>&f` пропускал.
test('открытие файла на запись видно во всех формах, а дескриптор целью не является', () => {
  assert.deepEqual(bash.commandTargets('echo x <> README.md'), ['README.md']);
  assert.deepEqual(bash.commandTargets('echo x >&README.md'), ['README.md']);
  assert.deepEqual(bash.commandTargets('echo x 2>&1'), []);
  assert.deepEqual(bash.commandTargets('cat a >&3'), []);
});

test('переход каталога на глубине не приписывает целям чужой каталог', () => {
  // Реальный каталог записи здесь /repo, а не /tmp: скобки — не подоболочка.
  assert.deepEqual(bash.commandTargets('cd /tmp; { cd /repo; echo x > f; }; echo x > g'), ['f', 'g']);
  // И та же осторожность на стороне ЧТЕНИЯ: назвать `/tmp/a.js` прочитанным
  // значило бы разрешить правку файла, которого никто не открывал.
  assert.deepEqual(bash.commandReads('cd /tmp; { cd /repo; }; cat a.js').targets, ['a.js']);
  // Обычный переход верхнего уровня по-прежнему резолвит — иначе проверка
  // зеленела бы от того, что отслеживание каталога сломано целиком.
  assert.deepEqual(bash.commandTargets('cd /tmp && echo x > f'), ['/tmp/f']);
  assert.deepEqual(bash.commandReads('cd /repo/lib && sed -n 1,5p a.js').targets, ['/repo/lib/a.js']);
});

// Непокрытое НАЗЫВАЕТСЯ. Разбора нет — пустой список целей и «команда ничего не
// пишет» с виду одно и то же, и выдать второе за первое значит промолчать про
// запись, которой никто не видел (решение 14). Проверяется на живом слое: адаптер
// уводится в несуществующий путь, как это выглядит на машине без разбора.
test('нет реализации разбора — ответ непокрытый С ИМЕНЕМ, а не «не менял»', async () => {
  const bare = await import(`../../.claude/hooks/lib/write-targets-bash.js?t=${Date.now()}`);
  const было = process.env.COMMAND_TREE_ADAPTER;
  process.env.COMMAND_TREE_ADAPTER = '/нет-такого-разбора';
  try {
    assert.equal(bare.commandTreeGap('cat > README.md'), 'command_tree');
    // И общая часть превращает это в непокрытое, а не в «мутации нет».
    const adapters = { commandWrites: (text) => ({ unsupported: bare.commandTreeGap(text) }) };
    const answer = tools.mutationOf({}, { kind: 'command', text: 'cat > README.md' }, adapters);
    assert.deepEqual(answer, { status: 'unsupported', capability: 'command_tree' });
  } finally {
    if (было === undefined) delete process.env.COMMAND_TREE_ADAPTER;
    else process.env.COMMAND_TREE_ADAPTER = было;
  }
  // А с разбором на месте ответ обычный: проверка ловит не «всё сломано».
  assert.equal(bash.commandTreeGap('cat > README.md'), '');
});

// Цель записи с ПОДСТАНОВКОЙ. Раскрыть её нечем: `$HOME` знает только оболочка.
// Обе крайности неверны — выдумать путь (`/notes.md`) значит соврать про
// конкретный файл, а выбросить цель значит сказать «команда ничего не пишет» и
// пропустить настоящую запись мимо гейта. Цель остаётся, но помечена.
test('цель записи с подстановкой помечена, а не выдумана и не потеряна', () => {
  const [target] = bash.commandTargets('cat > $HOME/notes.md');
  assert.ok(bash.isUnresolved(target), `цель не помечена: ${target}`);
  assert.notEqual(target, '/notes.md', 'путь, которого не существует, целью не является');
  // И она НЕ эфемерна: значит гейт её увидит.
  assert.equal(tools.isEphemeral(target), false);
  // Цель, СОСТОЯЩАЯ из одной подстановки, не исчезает: пустой список у гейта
  // значит «команда ничего не пишет», и запись прошла бы мимо него молча.
  const [only] = bash.commandTargets('cat a > $OUT');
  assert.ok(only && bash.isUnresolved(only), `цель потерялась: ${JSON.stringify(only)}`);
  assert.equal(tools.isEphemeral(only), false, 'а значит она под гейтом');
  // И каталог к метке не приклеивается: это не имя файла в нём.
  assert.deepEqual(bash.commandTargets('cd /repo && cat a > $f'), bash.commandTargets('cat a > $f'));
  // Внутри тела обёртки запуска нераскрытая цель по-прежнему отбрасывается —
  // так было и до перехода на дерево.
  assert.deepEqual(bash.commandTargets('bash -c "cat > $OUT"'), []);
  // Буквальная цель рядом остаётся буквальной.
  assert.deepEqual(bash.commandTargets('cat > README.md'), ['README.md']);
});

// Цель записи, названная не перенаправлением, а ОПЕРАНДОМ. Подстановка в ней
// прячется так же, а пустой список у гейта означает «команда ничего не пишет»:
// `cat x | tee "$LOG"` уходил бы мимо гейта молча.
test('операнд-цель с подстановкой помечен, а не потерян', () => {
  for (const cmd of ['tee $OUT', 'mv a.txt $DEST', 'cp a.txt $DEST/b.txt', 'sed -i s/a/b/ $F']) {
    const targets = bash.commandTargets(cmd);
    assert.ok(targets.some(bash.isUnresolved), `${cmd} → ${JSON.stringify(targets)}`);
  }
  // Буквальные операнды рядом остаются буквальными.
  assert.deepEqual(bash.commandTargets('tee out.txt'), ['out.txt']);
  assert.deepEqual(bash.commandTargets('cp a b'), ['b']);
});

// Пусковой префикс перед записью. Прежний разбор искал имя команды где угодно в
// куске и `sudo tee f` цель давал; дерево спрашивает ПЕРВОЕ слово. `sudo tee` —
// канонический способ записи в защищённый файл, и терять его нельзя.
test('пусковой префикс цель записи не прячет', () => {
  for (const cmd of ['sudo tee /repo/README.md', 'env FOO=1 tee /repo/README.md',
    'echo x | sudo tee -a /repo/README.md', 'sudo env FOO=1 tee /repo/README.md']) {
    assert.deepEqual(bash.commandTargets(cmd), ['/repo/README.md'], cmd);
  }
  // Имя записи ищется среди ВСЕХ слов, потому что списка пусковых префиксов не
  // хватает: `xargs`, `find -exec`, `su -c` и соседи прячут цель так же.
  for (const cmd of ['echo a | xargs tee /repo/README.md', 'find . -exec tee /repo/README.md ;',
    'nice -n 5 tee /repo/README.md', 'sudo -u user tee /repo/README.md']) {
    assert.deepEqual(bash.commandTargets(cmd), ['/repo/README.md'], cmd);
  }
  // ЦЕНА названа: слово `tee` не в начале звена даёт ЛИШНЮЮ цель — ровно как у
  // прежнего разбора. Лишняя цель гейта не открывает, пропущенная пропускает
  // правку мимо него.
  assert.deepEqual(bash.commandTargets('echo tee out.txt'), ['out.txt']);
  // А дерево при этом сильнее прежнего поиска по тексту: закавыченное сообщение
  // приходит ОДНИМ словом и целей не даёт.
  assert.deepEqual(bash.commandTargets('git commit -m "cp a b"'), []);
  // Снятие префикса живёт ТОЛЬКО на стороне записи: сними его у доказательства
  // чтения, и `sudo cat a` стало бы доказанным чтением, то есть гвард якоря
  // ослаб бы.
  assert.equal(bash.classifyCommand('sudo cat a.js').readOnly, false);
});

// Пускатель, заменяющий собой процесс, и ключи пускателей со СВОИМ значением.
// Тело обёртки за ними становилось невидимым: `wrapperBodyOf` спрашивает первое
// слово, а им оказывался пускатель или значение его ключа.
test('пускатель перед обёрткой запуска цель не прячет', () => {
  for (const cmd of ['exec bash -c "cat > /repo/README.md"',
    'sudo -u user bash -c "cat > /repo/README.md"',
    'env -u FOO bash -c "cat > /repo/README.md"',
    'exec -a имя bash -c "cat > /repo/README.md"']) {
    assert.deepEqual(bash.commandTargets(cmd), ['/repo/README.md'], cmd);
  }
});

// Терминатор перебора приходит операндом и путём не является: назвав его целью,
// гейт отбивал бы команду, у которой настоящая цель — устройство.
test('терминатор перебора целью записи не становится', () => {
  assert.deepEqual(bash.commandTargets(String.raw`find . -name '*.log' -exec tee /dev/null \;`), []);
  // А настоящая цель за тем же перебором видна.
  assert.deepEqual(bash.commandTargets(String.raw`find . -exec tee /repo/README.md \;`), ['/repo/README.md']);
});
