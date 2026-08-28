// Инструмент «что делает вызов»: один ответ на все поверхности гейта — правку
// файла, команду, запись в базу, незнакомый инструмент.
//
// Зачем отдельным модулем и отдельными проверками. Раньше определение записи
// жило в двух гвардах разными словами: гейт смотрел на цели записи, гвард якоря
// требовал от команды доказать, что она только читает. Одна и та же команда у
// одного была записью, у другого нет, и увидеть это можно было только на живом
// прогоне. Здесь оно одно, и его видно проверкой без события.
//
// Расширение .mjs, а не .js: в каталоге тестов нет манифеста модулей.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { writeIntent } = await import('../../.claude/hooks/lib/write-intent.js');

const bash = (command) => writeIntent({ tool: 'Bash', input: { command } });
const writes = (command) => bash(command).writes;

// --- команды: цели записи ----------------------------------------------------

test('перенаправление и правка на месте — запись', () => {
  for (const cmd of [
    'echo x > README.md',
    'cat >> README.md',
    "sed -i 's/a/b/' README.md",
    'cp /tmp/gen.go kinowatch/real.go',
  ]) {
    assert.equal(writes(cmd), true, cmd);
  }
});

test('чтение целей не даёт', () => {
  for (const cmd of [
    'cat README.md',
    'cat README.md | grep -c x',
    'ls -la',
  ]) {
    assert.equal(writes(cmd), false, cmd);
  }
});

// --- команды: свой список тех, что меняют дерево ------------------------------
// Целей записи эти команды не дают вовсе: разбор ищет перенаправление и
// копирование, а они меняют дерево сами. До появления списка каждая из них
// проходила гейт молча.

test('команды, меняющие дерево, — запись', () => {
  for (const cmd of [
    'rm README.md',
    'rm -rf .claude/hooks',
    'mkdir build',
    'touch newfile.txt',
    'chmod +x install.sh',
    'ln -sf a b',
    'truncate -s 0 README.md',
    'git checkout -- README.md',
    'git restore README.md',
    'git clean -fd',
    'git reset --hard',
  ]) {
    assert.equal(writes(cmd), true, cmd);
  }
});

test('временные цели перекрывают имя команды', () => {
  // Иначе уборка своего же мусора упиралась бы в гейт: у `rm /tmp/f` разбор
  // целей молчит, а имя опознано — решение принимается по целям, не по имени.
  for (const cmd of ['rm /tmp/scratch.txt', 'rm -rf /tmp/work', 'mkdir /tmp/work']) {
    const d = bash(cmd);
    assert.equal(d.writes, false, cmd);
    assert.equal(d.ephemeralOnly, true, cmd);
  }
});

test('команда из списка без путей в аргументах — запись без целей', () => {
  // Пропускать её нечем: эфемерность проверять не на чем.
  const d = bash('git clean -fd');
  assert.equal(d.writes, true);
  assert.equal(d.realTargets.length, 0);
});

test('имя команды в тексте целью не становится', () => {
  // Кавычки разбор снимает, слова внутри остаются: мутатор засчитывается только
  // в начале своего куска команды.
  for (const cmd of ["echo 'rm README.md'", 'printf "%s\\n" "mkdir build"']) {
    assert.equal(writes(cmd), false, cmd);
  }
});

// --- команды: доказательств чтения инструмент не требует ---------------------
// Политика гейта обратная гварду: он ищет признаки записи, а не доказательства
// чтения. На этом держится то, ради чего всё затевалось, — незнакомый клиент
// проходит, пока не пишет.

test('незнакомая команда без признаков записи проходит', () => {
  for (const cmd of [
    'codex exec "разбери план"',
    'craft-sync --backlinks abc',
    '[ -n "$VAR" ]',
    'command -v codex',
    "sed -e 's/x/y/' README.md",
    'awk -f script.awk data.txt',
  ]) {
    assert.equal(writes(cmd), false, cmd);
  }
});

test('фиксация уже сверенного записью не считается', () => {
  // Содержимого рабочего дерева она не меняет, а всё, что в неё попадает, гейт
  // просудил на самих правках.
  for (const cmd of [
    'git add -A',
    'git add docs',
    'git commit -m "текст"',
    'git push -u origin ветка',
  ]) {
    assert.equal(writes(cmd), false, cmd);
  }
});

// --- прочие поверхности ------------------------------------------------------

test('правка файла судится по цели', () => {
  const edit = (file_path) => writeIntent({ tool: 'Edit', input: { file_path, old_string: 'a', new_string: 'b' } });
  assert.equal(edit('/home/user/craft/README.md').writes, true);
  assert.equal(edit('/tmp/scratch.md').writes, false);
  assert.equal(edit('/root/.claude/plans/some-plan.md').writes, false);
  assert.equal(writeIntent({ tool: 'NotebookEdit', input: { notebook_path: '/home/user/craft/a.ipynb' } }).writes, true);
});

test('запись в базу — всегда запись', () => {
  assert.equal(writeIntent({ tool: 'mcp__Craft__craft_write', input: { command: 'blocks add --id X --json {}' } }).writes, true);
  assert.equal(writeIntent({ tool: 'mcp__ece65cbd__craft_write', input: { command: 'blocks add' } }).writes, true);
});

test('читающие инструменты и ход самой сессии — не запись', () => {
  for (const tool of ['Read', 'Grep', 'Glob', 'WebFetch', 'BashOutput', 'TaskCreate', 'ExitPlanMode', 'AskUserQuestion']) {
    assert.equal(writeIntent({ tool, input: {} }).writes, false, tool);
  }
});

test('MCP судится по глаголу в имени операции', () => {
  assert.equal(writeIntent({ tool: 'mcp__x__list_repos', input: {} }).writes, false);
  assert.equal(writeIntent({ tool: 'mcp__x__craft_read', input: {} }).writes, false);
  assert.equal(writeIntent({ tool: 'mcp__x__create_pull_request', input: {} }).writes, true);
});

test('подагенты делятся на читающих и правящих', () => {
  const agent = (subagent_type) => writeIntent({ tool: 'Task', input: { subagent_type } });
  assert.equal(agent('Explore').writes, false);
  assert.equal(agent('plan-critic').writes, false);
  assert.equal(agent('general-purpose').writes, true);
});

test('обслуживание собственного хода — не запись', () => {
  assert.equal(writeIntent({ tool: 'mcp__gh__subscribe_pr_activity', input: {} }).writes, false);
  assert.equal(writeIntent({ tool: 'mcp__ccr__send_later', input: {} }).writes, false);
});

// --- форма ответа ------------------------------------------------------------

test('ответ несёт причину и цели', () => {
  const d = bash('echo x > README.md');
  assert.match(d.why, /\S/);
  assert.ok(d.realTargets.some((t) => t.includes('README.md')), 'цель названа');
  assert.equal(d.kind, 'command');
});
