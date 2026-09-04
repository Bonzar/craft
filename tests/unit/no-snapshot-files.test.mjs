// Гвард снимков Craft (tools/no-snapshot-files.js) — на временных git-репозиториях,
// без сети. Кейс-инцидент первый: ветка, где хук стал писать роутер в .craft/,
// а .gitignore закрывал только .claude/, унесла тысячу строк памяти в публичную
// историю. Гвард обязан ловить это по маске имени в любом каталоге, а заглушки
// фикстур пропускать — но только пока они заглушки.
//
// Расширение .mjs — как у соседей: без манифеста модулей .js читается как
// обычный скрипт.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const GUARD = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'tools', 'no-snapshot-files.js');
// Маркер собран из частей: тест сам не должен выглядеть снимком.
const MEMORY_HEADER = ['Память', ' (регенерируемая)'].join('');
const IDENTITY = ['Общий', ' контекст'].join('');
const TITLE = ['<pageTitle>', '🧠 Память агента</pageTitle>'].join('');
const SNAPSHOT_HEADER = ['=== Craft: «⚙️ SKILL: Разбор инцидента», авто-обновлён', ' SessionStart-хуком (2026-08-03T13:55:34Z) ==='].join('');

function sh(cwd, cmd, args) {
  const r = spawnSync(cmd, args, { cwd, encoding: 'utf8' });
  return r;
}

// Песочницы (по репозиторию на кейс) сносятся одним разом в конце файла:
// временный каталог здесь же служит каталогом состояния хуков.
const sandboxes = [];
after(() => {
  for (const dir of sandboxes) fs.rmSync(dir, { recursive: true, force: true });
});

function repo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'no-snapshot-'));
  sandboxes.push(dir);
  sh(dir, 'git', ['init', '-q', '-b', 'main']);
  sh(dir, 'git', ['config', 'user.email', 't@example.com']);
  sh(dir, 'git', ['config', 'user.name', 't']);
  return dir;
}

function write(dir, file, text) {
  const full = path.join(dir, file);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, text);
}

function commitAll(dir, msg = 'c') {
  sh(dir, 'git', ['add', '-A']);
  const r = sh(dir, 'git', ['commit', '-q', '-m', msg]);
  assert.equal(r.status, 0, r.stderr);
}

function guard(dir, ...args) {
  return sh(dir, 'node', [GUARD, ...args]);
}

test('чистое дерево проходит', () => {
  const dir = repo();
  write(dir, 'README.md', 'обычный док, упоминает страницу 🧠 Память — это не снимок\n');
  // «Общий контекст» — обычный заголовок архитектурного дока: сам по себе не улика.
  write(dir, 'docs/arch.md', `# ${IDENTITY}\n\nкак устроена система\n`);
  write(dir, 'tools/x.js', `const h = '${MEMORY_HEADER}'; // код ссылается на заголовок\n`);
  commitAll(dir);
  const r = guard(dir, '--tree', 'HEAD');
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /снимков Craft нет/);
});

test('инцидент: роутер в .craft/ ловится по маске в любом каталоге', () => {
  const dir = repo();
  write(dir, '.craft/router-context.md', `${TITLE}\n${MEMORY_HEADER}\n`);
  write(dir, '.craft/incident-context.md', 'skill doc\n');
  write(dir, 'tests/hooks/fixtures/warm-cache/.craft/router-context.md', `${TITLE}\n${MEMORY_HEADER}\n`);
  write(dir, 'some/dir/craft-gate-exempt-scope.txt', 'ids\n');
  commitAll(dir);
  const r = guard(dir, '--tree', 'HEAD');
  assert.equal(r.status, 1);
  for (const p of ['.craft/router-context.md', '.craft/incident-context.md',
    'tests/hooks/fixtures/warm-cache/.craft/router-context.md', 'some/dir/craft-gate-exempt-scope.txt']) {
    assert.match(r.stderr, new RegExp(p.replace(/[.]/g, '\\.')), `не назван ${p}`);
  }
});

test('заглушки фикстур warm-cache разрешены, пока они заглушки', () => {
  const dir = repo();
  write(dir, 'tests/hooks/fixtures/warm-cache/.claude/craft-router-context.md', 'роутер памяти (фикстура прогретого кэша)\n');
  write(dir, 'tests/hooks/fixtures/warm-cache/.claude/craft-incident-context.md', 'skill doc stub\n');
  write(dir, 'tests/hooks/fixtures/warm-cache/.craft/router-context.md', 'роутер памяти (фикстура прогретого кэша)\n');
  commitAll(dir);
  assert.equal(guard(dir, '--tree', 'HEAD').status, 0);

  write(dir, 'tests/hooks/fixtures/warm-cache/.claude/craft-router-context.md', `${TITLE}\n…\n${IDENTITY}\n`);
  commitAll(dir, 'real router into fixture');
  const r = guard(dir, '--tree', 'HEAD');
  assert.equal(r.status, 1);
  assert.match(r.stderr, /разрешённая фикстура, но содержимое — настоящий снимок/);
  assert.match(r.stderr, /craft-router-context\.md/);

  // Снимок SKILL-дока инцидента под именем фикстуры — тоже снимок: у него шапка
  // инжект-хука, а роутерных признаков нет.
  write(dir, 'tests/hooks/fixtures/warm-cache/.claude/craft-router-context.md', 'заглушка\n');
  write(dir, 'tests/hooks/fixtures/warm-cache/.claude/craft-incident-context.md', `${SNAPSHOT_HEADER}\n<page id="x">\n  <pageTitle>⚙️ SKILL: Разбор инцидента</pageTitle>\n`);
  commitAll(dir, 'real incident doc into fixture');
  const r2 = guard(dir, '--tree', 'HEAD');
  assert.equal(r2.status, 1);
  assert.match(r2.stderr, /craft-incident-context\.md: разрешённая фикстура, но содержимое — настоящий снимок/);
});

test('роутер под чужим именем ловится по содержимому', () => {
  const dir = repo();
  write(dir, 'docs/notes.md', `# заметка\n\n${MEMORY_HEADER}\n\n${IDENTITY}\n`);
  commitAll(dir);
  const r = guard(dir, '--tree', 'HEAD');
  assert.equal(r.status, 1);
  assert.match(r.stderr, /docs\/notes\.md: содержимое несёт признаки снимка Craft/);
});

test('--tree проверяет названный коммит, не только HEAD', () => {
  const dir = repo();
  write(dir, '.claude/craft-router-context.md', 'snapshot\n');
  commitAll(dir, 'leak');
  sh(dir, 'git', ['rm', '-q', '.claude/craft-router-context.md']);
  commitAll(dir, 'cleanup');
  assert.equal(guard(dir, '--tree', 'HEAD').status, 0);
  assert.equal(guard(dir, '--tree', 'HEAD~1').status, 1);
});

test('--staged смотрит индекс: pre-commit ловит снимок до коммита', () => {
  const dir = repo();
  write(dir, 'a.md', 'ok\n');
  commitAll(dir);
  write(dir, '.claude/craft-incident-context.md', 'snapshot\n');
  assert.equal(guard(dir, '--staged').status, 0, 'незастейдженный файл индекс не трогает');
  sh(dir, 'git', ['add', '.claude/craft-incident-context.md']);
  const r = guard(dir, '--staged');
  assert.equal(r.status, 1);
  assert.match(r.stderr, /\.claude\/craft-incident-context\.md/);
});

test('pre-commit-хук из .githooks валит коммит со снимком', () => {
  const dir = repo();
  const hooks = path.resolve(path.dirname(GUARD), '..', '.githooks');
  // Хук зовёт tools/no-snapshot-files.js относительно корня репо — кладём копию.
  write(dir, 'tools/no-snapshot-files.js', fs.readFileSync(GUARD, 'utf8'));
  fs.mkdirSync(path.join(dir, '.githooks'), { recursive: true });
  fs.copyFileSync(path.join(hooks, 'pre-commit'), path.join(dir, '.githooks', 'pre-commit'));
  fs.chmodSync(path.join(dir, '.githooks', 'pre-commit'), 0o755);
  sh(dir, 'git', ['config', 'core.hooksPath', '.githooks']);
  commitAll(dir, 'tooling');

  write(dir, '.craft/router-context.md', 'snapshot\n');
  sh(dir, 'git', ['add', '-A']);
  const bad = sh(dir, 'git', ['commit', '-q', '-m', 'leak']);
  assert.notEqual(bad.status, 0, 'коммит со снимком прошёл');
  assert.match(bad.stderr, /\.craft\/router-context\.md/);

  sh(dir, 'git', ['rm', '-q', '--cached', '.craft/router-context.md']);
  write(dir, 'ok.md', 'fine\n');
  sh(dir, 'git', ['add', 'ok.md']);
  const good = sh(dir, 'git', ['commit', '-q', '-m', 'clean']);
  assert.equal(good.status, 0, good.stderr);
});

test('неизвестный режим — exit 2', () => {
  const dir = repo();
  assert.equal(guard(dir, '--bogus').status, 2);
});
