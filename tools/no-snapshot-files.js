#!/usr/bin/env node
// Гвард: в git не должно быть снимков живого Craft.
//
// Снимки роутера (.claude/craft-router-context.md, .craft/router-context.md,
// .codex/…), снимок SKILL-дока инцидента, кэш предодобренной зоны и каталоги
// прогретого кэша — регенерируемая память о жизни Влада. Их пишут SessionStart-
// хуки в чекаут, .gitignore их закрывает, но закрывает по точному пути: ветка,
// где хук стал писать в другой каталог, унесла роутер на тысячу строк в
// публичную историю. Поэтому проверка идёт по МАСКЕ имени, а не по списку
// путей, и стоит в двух местах: pre-commit (индекс) и CI (дерево коммита).
//
// Запуск:
//   node tools/no-snapshot-files.js --tree [rev]   # дерево коммита (по умолчанию HEAD)
//   node tools/no-snapshot-files.js --staged       # индекс перед коммитом
//   node tools/no-snapshot-files.js --files a b c  # проверить файлы с диска
//
// Что считается нарушением:
//   1. Путь, в котором есть сегмент по маске (router-context, incident-context,
//      craft-gate-exempt-scope, warm-cache) — где угодно, кроме явно
//      разрешённых фикстур ниже.
//   2. Разрешённая фикстура, чьё содержимое несёт признаки настоящего снимка:
//      шапка, которую пишет инжект-хук («авто-обновлён SessionStart-хуком»),
//      заголовок «Память (регенерируемая)», XML-титул «<pageTitle>🧠 Память»
//      (упоминание страницы в обычном доке — не снимок). Секция «Общий
//      контекст» — признак слабый: обычный док с таким заголовком законен,
//      она считается только вместе с сильным. Фикстура — заглушка на строку,
//      а не копия живого дока, роутера или SKILL-дока.
//   3. Любой .md/.txt с теми же признаками: снимок, сохранённый под другим
//      именем, — всё равно снимок.
//
// Exit 0 — чисто, 1 — найдены снимки (список в stderr), 2 — ошибка запуска.
// Зависимости: только node и git.
//
// Проверка при подозрении на утечку — коротко; полностью, с оговорками про
// свежий клон и серверные рефы, — в tests/README.md, раздел «Проверка при
// утечке снимка». Работать в копии, которая объект держала, и по ОДНОМУ списку
// ревизий на оба захода, включая НЕДОСТИЖИМЫЕ коммиты: снимок со снесённой
// ветки в `rev-list` не попадает, а объект остаётся, и ради него всё и делается.
//
//   git fetch origin '+refs/*:refs/remotes/origin/*'
//   revs() { { git rev-list --all --reflog
//              git fsck --unreachable --no-reflogs 2>/dev/null | awk '$2=="commit"{print $3}'
//            } | sort -u; }
//   # заход 1, по именам: маски берутся из MASKS ЭТОГО файла, а не переписываются
//   revs | xargs -r git show --name-only --format= -- | sort -u | grep -iEf <(
//     node -e 'const {MASKS}=require("./tools/no-snapshot-files.js");
//              console.log(MASKS.map((m) => m.source).join("\n"))')
//   # заход 2, по содержимому: вывод гварда НУЖЕН — в нём имя сработавшего файла,
//   # и код 1 (утечка) отличается от кода 2 (ошибка запуска)
//   revs | while read -r rev; do
//     out="$(node tools/no-snapshot-files.js --tree "$rev" 2>&1)"
//     case $? in 1) printf 'утечка: %s\n%s\n' "$rev" "$out";; 0) ;;
//                *) printf 'ошибка: %s\n%s\n' "$rev" "$out";; esac
//   done
//
// Чего эта проверка НЕ покрывает: болтающийся блоб, не лежащий ни в одном
// дереве, — оба захода ходят по коммитам; для него поиск по содержимому среди
// `git fsck --unreachable` блобов, он тоже описан в tests/README.md. Заход 1 к
// тому же слеп к КОММИТАМ СЛИЯНИЯ: `git show` печатает у них не файлы слияния, а
// комбинированный дифф — файл, совпавший хотя бы с одним родителем, в список не
// попадёт. Их закрывает заход 2 — он смотрит ДЕРЕВО каждой ревизии, а не её
// дифф.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const MASKS = [/router-context/i, /incident-context/i, /craft-gate-exempt-scope/i, /warm-cache/i];

// Фикстуры кейсов inject-cache-reuse: хук ищет снимок по точному имени, поэтому
// заглушка обязана называться как настоящий файл. Только эти пути, только с
// содержимым-заглушкой.
const ALLOWED_FIXTURES = new Set([
  'tests/hooks/fixtures/warm-cache/.claude/craft-router-context.md',
  'tests/hooks/fixtures/warm-cache/.claude/craft-incident-context.md',
  // Ветки с ядром в core/ пишут снимки в .craft/ — та же заглушка под тем же кейсом.
  'tests/hooks/fixtures/warm-cache/.craft/router-context.md',
]);

// Признаки настоящего снимка. Строки собраны из частей, чтобы сам гвард
// (и его тест) не ловился собственной проверкой содержимого. Сильный признак
// достаточен сам по себе; слабый — только рядом с сильным.
const STRONG_MARKERS = [
  ['авто-обновлён', ' SessionStart-хуком'].join(''), // шапка любого снимка инжект-хука
  ['Память', ' (регенерируемая)'].join(''),
  ['<pageTitle>', '🧠 Память'].join(''),
];
const WEAK_MARKERS = [
  ['Общий', ' контекст'].join(''),
];
const CONTENT_MARKERS = [...STRONG_MARKERS, ...WEAK_MARKERS];

const CONTENT_SCAN_EXT = new Set(['.md', '.txt']);

function fail(message, code = 2) {
  process.stderr.write(`[no-snapshot-files] ${message}\n`);
  process.exit(code);
}

function git(args, opts = {}) {
  const r = spawnSync('git', args, { encoding: 'buffer', maxBuffer: 64 * 1024 * 1024, ...opts });
  if (r.status !== 0) {
    throw new Error(`git ${args.join(' ')} failed: ${r.stderr.toString('utf8').trim()}`);
  }
  return r.stdout;
}

function splitZ(buf) {
  return buf.toString('utf8').split('\0').filter(Boolean);
}

function pathMatchesMask(file) {
  return file.split('/').some((seg) => MASKS.some((m) => m.test(seg)));
}

function contentMarkers(text) {
  const strong = STRONG_MARKERS.filter((m) => text.includes(m));
  if (!strong.length) return [];
  return [...strong, ...WEAK_MARKERS.filter((m) => text.includes(m))];
}

// Источник списка файлов и их содержимого — по режиму.
function sourceFor(argv) {
  const mode = argv[0] || '--tree';
  if (mode === '--tree') {
    const rev = argv[1] || 'HEAD';
    return {
      label: `дерево ${rev}`,
      list: () => splitZ(git(['ls-tree', '-r', '-z', '--name-only', rev])),
      read: (file) => git(['show', `${rev}:${file}`]).toString('utf8'),
    };
  }
  if (mode === '--staged') {
    return {
      label: 'индекс (staged)',
      list: () => splitZ(git(['diff', '--cached', '-z', '--name-only', '--diff-filter=ACMR'])),
      read: (file) => git(['show', `:${file}`]).toString('utf8'),
    };
  }
  if (mode === '--files') {
    const files = argv.slice(1);
    if (!files.length) fail('--files: не переданы файлы');
    return {
      label: 'файлы',
      list: () => files,
      read: (file) => fs.readFileSync(file, 'utf8'),
    };
  }
  fail(`неизвестный режим ${mode}; ожидается --tree [rev] | --staged | --files …`);
  return null;
}

function check(source) {
  const violations = [];
  for (const file of source.list()) {
    const byMask = pathMatchesMask(file);
    const allowed = ALLOWED_FIXTURES.has(file);
    if (byMask && !allowed) {
      violations.push(`${file}: путь по маске снимка Craft`);
      continue;
    }
    const scan = allowed || CONTENT_SCAN_EXT.has(path.extname(file).toLowerCase());
    if (!scan) continue;
    let text;
    try {
      text = source.read(file);
    } catch {
      continue; // бинарник, симлинк, удалённый файл — не снимок
    }
    const hits = contentMarkers(text);
    if (hits.length) {
      const where = allowed ? 'разрешённая фикстура, но содержимое — настоящий снимок' : 'содержимое несёт признаки снимка Craft';
      violations.push(`${file}: ${where} (${hits.map((h) => `«${h}»`).join(', ')})`);
    }
  }
  return violations;
}

if (require.main === module) {
  let source;
  try {
    source = sourceFor(process.argv.slice(2));
    const violations = check(source);
    if (violations.length) {
      process.stderr.write(`[no-snapshot-files] ${source.label}: найдены снимки живого Craft — в git им не место:\n`);
      for (const v of violations) process.stderr.write(`  - ${v}\n`);
      process.stderr.write('Убери файл из коммита (git rm --cached <путь>) и проверь .gitignore; регенерируемые снимки пишет SessionStart-хук.\n');
      process.exit(1);
    }
    process.stdout.write(`[no-snapshot-files] ${source.label}: снимков Craft нет\n`);
  } catch (e) {
    fail(e.message);
  }
}

module.exports = { MASKS, ALLOWED_FIXTURES, STRONG_MARKERS, WEAK_MARKERS, CONTENT_MARKERS, pathMatchesMask, contentMarkers, check };
