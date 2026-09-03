// Признаки вызова: трогает ли он мир и чем именно. Здесь живут имена
// инструментов, поэтому и кейсы про них — здесь, а не в ядре метрик.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const tools = await import('../../.claude/hooks/lib/write-targets.js');
const metrics = await import('../../.claude/hooks/lib/metrics.js');


test('читающие команды гита мутацией не считаются', () => {
  for (const cmd of ['git diff --merge-base main', 'git log --oneline -- lib/tag.js',
    'git show HEAD:src/reset.js', 'git stash list', 'git stash show', 'git tag',
    'git tag --list', "git tag -l 'v*'", 'git branch', 'git branch -a', 'git branch -r',
    'git remote', 'git remote -v', 'git remote show origin', 'git worktree list',
    'git worktree list', 'git status']) {
    assert.equal(tools.looksMutating('Bash', { command: cmd }), false, cmd);
  }
  for (const cmd of ['git commit -m x', 'git -C /repo push origin main', 'git stash push -m wip',
    'git stash', 'git tag v1', 'git checkout -b feature',
    // Правящие формы тех же подкоманд и сетевые вызовы: они пишут ссылки и
    // объекты в локальный репозиторий, и ход с ними ходом без изменений не был.
    'git fetch origin', 'git pull', 'git branch feature', 'git branch -d old',
    'git remote add origin https://example.invalid/r.git', 'git worktree add /tmp/w']) {
    assert.equal(tools.looksMutating('Bash', { command: cmd }), true, cmd);
  }
});

// В цепочке смотрятся ВСЕ вызовы: по одному первому «git status && git commit»
// читался бы как ход без единого изменения, а слово в кавычках — как правка.
test('правка видна в любом месте цепочки, а слово в кавычках правкой не считается', () => {
  assert.equal(tools.looksMutating('Bash', { command: 'git status && git commit -m x' }), true);
  assert.equal(tools.looksMutating('Bash', { command: 'git log --oneline | head; git tag v1' }), true);
  assert.equal(tools.looksMutating('Bash', { command: "printf 'git commit -m x'" }), false);
  assert.equal(tools.looksMutating('Bash', { command: 'echo git push' }), false);
});

test('работа через подагента считается прогрессом, чтение — нет', () => {
  assert.equal(tools.looksMutating('Task', { subagent_type: 'general-purpose' }), true);
  assert.equal(tools.looksMutating('Task', { subagent_type: 'Explore' }), false, 'разведка мир не трогает');
  assert.equal(tools.looksMutating('mcp__Claude_Code_Remote__subscribe_pr_activity', {}), false,
    'обслуживание своего хода — не правка мира, как и у гейта');
});

test('прогресс: удавшаяся мутация через Bash или чужой MCP-инструмент записи', () => {
  assert.equal(tools.looksMutating('Bash', { command: 'echo x >> README.md' }), true);
  assert.equal(tools.looksMutating('Bash', { command: 'git commit -m x' }), true);
  assert.equal(tools.looksMutating('Bash', { command: 'ls -la' }), false);
  assert.equal(tools.looksMutating('Bash', { command: 'echo x > /tmp/scratch.txt' }), false, 'эфемерная цель — не мутация');
  assert.equal(tools.looksMutating('mcp__github__create_pull_request', {}), true);
  assert.equal(tools.looksMutating('mcp__github__list_pull_requests', {}), false);
  assert.equal(tools.looksMutating('Edit', { file_path: '/home/user/craft/README.md' }), true);
  assert.equal(tools.looksMutating('Edit', { file_path: '/tmp/scratch.txt' }), false,
    'правка эфемерной цели прогрессом не считается — как и запись в неё через шелл');
  assert.equal(tools.looksMutating('Edit', {}), false, 'правка без цели мир не меняет');
  assert.equal(tools.looksMutating('Read', {}), false);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: true }, { error: false }), true);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: true }, { error: true }), false);
  assert.equal(metrics.isProgress({ tool: 'Bash', mutates: false }, { error: false }), false);
});
