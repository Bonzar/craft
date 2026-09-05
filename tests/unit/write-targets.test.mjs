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
    { reads: false, proven: true, targets: [] });
  assert.equal(bash.commandReads('rm -rf /repo/x').proven, true, 'про удаление разбор всё знает');
  assert.equal(bash.commandReads('node сборка.js').proven, false, 'а про чужой запуск — ничего');
  assert.deepEqual(bash.commandReads(''), { reads: false, proven: true, targets: [] });
  assert.equal(bash.commandReads('pwd').reads, true, 'читающая команда без файлов — всё равно чтение');
  assert.deepEqual(bash.commandReads('pwd').targets, []);
});
