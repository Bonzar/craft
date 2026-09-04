// Метка репозитория под git: разбор адреса remote. Форма метки — то, по чему
// сводки одной работы складываются вместе, поэтому выдуманный владелец или
// порт, попавший в имя, портит всю выборку.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

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

// Ответ «игнорируется ли путь» запоминается на жизнь процесса: один вызов хука
// спрашивает про один и тот же путь до трёх раз (гейт, якорь сессии, метрики), а
// у команды с несколькими целями — на каждую цель. Проверяется не счётчиком
// форков, а СЛЕДСТВИЕМ памяти: ответ не меняется вслед за .gitignore.
test('ответ об игнорировании пути запоминается в пределах процесса', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ignored-memo-'));
  try {
    const G = (...args) => spawnSync('git', ['-C', dir, ...args], { stdio: 'ignore' });
    G('init', '--quiet');
    fs.writeFileSync(path.join(dir, '.gitignore'), 'memo.log\n');
    fs.writeFileSync(path.join(dir, 'memo.log'), 'x');

    const before = repo.isIgnored('memo.log', dir);
    fs.writeFileSync(path.join(dir, '.gitignore'), '');
    const after = repo.isIgnored('memo.log', dir);

    assert.equal(before, true, 'путь игнорируется по .gitignore');
    assert.equal(after, true, 'второй ответ пришёл из памяти, а не из нового вызова git');
    assert.equal(repo.isIgnored('other.log', dir), false, 'память не отвечает за чужой путь');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
