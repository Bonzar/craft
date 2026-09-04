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
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: true }, { error: false }), true);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: true }, { error: true }), false);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: false }, { error: false }), false);
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
  const hash = (tool, input) => metrics.callHash(tool, claude.semanticInput(tool, input));
  assert.equal(
    hash('Bash', { command: 'git push', description: 'Push branch', timeout: 120000 }),
    hash('Bash', { command: 'git push', description: 'Push the branch to origin' }),
    'переписанное описание не должно делать повтор другим вызовом',
  );
  assert.notEqual(hash('Bash', { command: 'git push' }), hash('Bash', { command: 'git status' }));
  assert.equal(
    hash('KillShell', { shell_id: '1' }),
    hash('KillShell', { shell_id: '2' }),
    'номер фонового запуска смыслом вызова не является',
  );
  // А чтение чужого вывода — является: у него идентификатор говорит, ЧЕЙ вывод.
  assert.notEqual(hash('BashOutput', { bash_id: 'a' }), hash('BashOutput', { bash_id: 'b' }));
  // Список служебных полей — ПО ИНСТРУМЕНТУ: у правки `description` нет вовсе, и
  // общий список отсеивал бы поле там, где оно могло быть смыслом.
  assert.notEqual(
    hash('Edit', { file_path: 'a', description: 'x' }),
    hash('Edit', { file_path: 'a', description: 'y' }),
  );
  assert.deepEqual(claude.semanticInput('Edit', { file_path: 'a' }), { file_path: 'a' });
  assert.deepEqual(claude.semanticInput('Bash', undefined), {});
});
