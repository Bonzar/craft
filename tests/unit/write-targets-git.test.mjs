// Разбор вызовов git в команде: что здесь считается отправкой.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const git = await import('../../.claude/hooks/lib/write-targets-git.js');


test('пуш опознаётся по подкоманде, а не по слову в строке', () => {
  assert.equal(git.looksLikePush('git push -u origin main'), true);
  assert.equal(git.looksLikePush('git push'), true);
  assert.equal(git.looksLikePush('git stash push -m wip'), false);
  assert.equal(git.looksLikePush('git commit -m "fix push hook"'), false);
  assert.equal(git.looksLikePush('git log --grep push'), false);
  assert.equal(git.looksLikePush('git push --dry-run'), false, 'пробный прогон не пуш');
});

// Пуш опознаётся по вызову git, а не по слову в строке: `printf 'git push'`
// пушем не является, и исход сессии на нём не помечается пушем.
test('пуш: вызов git, а не слово где угодно в команде', () => {
  assert.equal(git.looksLikePush('git push -u origin main'), true);
  assert.equal(git.looksLikePush('cd /repo && git push'), true);
  assert.equal(git.looksLikePush('git -C /repo push'), true);
  assert.equal(git.looksLikePush("printf 'git push'"), false, 'слово в кавычках командой не является');
  assert.equal(git.looksLikePush('echo git push'), false, 'аргумент чужой команды');
  assert.equal(git.looksLikePush('git commit -m "fix push"'), false);
  assert.equal(git.looksLikePush('git stash push'), false);
  assert.equal(git.looksLikePush('git push --dry-run'), false);
});
