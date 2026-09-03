// Метка репозитория под git: разбор адреса remote. Форма метки — то, по чему
// сводки одной работы складываются вместе, поэтому выдуманный владелец или
// порт, попавший в имя, портит всю выборку.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const repo = await import('../../.claude/hooks/lib/repo-git.js');


test('remote нормализуется до host/owner/repo', () => {
  assert.equal(repo.normalizeRemote('https://github.com/Bonzar/craft.git'), 'github.com/Bonzar/craft');
  assert.equal(repo.normalizeRemote('https://x-access-token:abc@github.com/Bonzar/craft'), 'github.com/Bonzar/craft');
  assert.equal(repo.normalizeRemote('git@github.com:Bonzar/craft.git'), 'github.com/Bonzar/craft');
  assert.equal(repo.normalizeRemote(''), '');
});

test('remote с портом не превращает порт во владельца', () => {
  assert.equal(repo.normalizeRemote('ssh://git@github.com:2222/Bonzar/craft.git'), 'github.com/Bonzar/craft');
  assert.equal(repo.normalizeRemote('https://github.com:443/Bonzar/craft.git'), 'github.com/Bonzar/craft');
  assert.equal(repo.normalizeRemote('git@github.com:Bonzar/craft.git'), 'github.com/Bonzar/craft', 'scp-форма цела');
});
