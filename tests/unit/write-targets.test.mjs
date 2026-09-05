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
