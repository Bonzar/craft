// Разбор вызовов git в команде: что здесь считается отправкой.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const git = await import('../../.claude/hooks/lib/write-targets-git.js');


// Пуш — это ВЫЗОВ git с подкомандой push, а не слово в строке: `git stash push`,
// `git commit -m "fix push"`, `git log --grep push`, `printf 'git push'` и
// `echo git push` пушем не являются. Пробный прогон — тоже.
test('пуш опознаётся по вызову git и его подкоманде', () => {
  for (const cmd of ['git push', 'git push -u origin main', 'cd /repo && git push', 'git -C /repo push']) {
    assert.equal(git.looksLikePush(cmd), true, cmd);
  }
  for (const cmd of ['git stash push', 'git stash push -m wip', 'git commit -m "fix push"',
    'git log --grep push', "printf 'git push'", 'echo git push', 'git push --dry-run']) {
    assert.equal(git.looksLikePush(cmd), false, cmd);
  }
});
