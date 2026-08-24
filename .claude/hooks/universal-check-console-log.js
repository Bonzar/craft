#!/usr/bin/env node
// Stop-хук: предупреждение (НЕ блок) о console.log в JS/TS-файлах, правленных
// за сессию. Портирован из ECC (check-console-log.js): отладочные логи легко
// забываются в коде — хук напоминает убрать их до коммита.
//
// Механика: из события Stop берётся transcript_path, из JSONL транскрипта —
// file_path всех вызовов Edit/Write/MultiEdit за сессию. Файлы фильтруются:
// существующие .ts/.tsx/.js/.jsx вне зависимостей и тестов. В отфильтрованных
// ищется console.log; строки с маркером // keep-console (осознанный лог) не
// считаются.
//
// Нашёл → предупреждение со списком файл:строка. Ничего не нашёл или нет
// транскрипта → тихий выход. Fail open на всём неожиданном.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { editedFiles, sourceFiles } from './lib/transcript.js';

const { raw, event, transcript } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
if (!transcript || !fs.existsSync(transcript)) process.exit(0);

const files = sourceFiles(editedFiles(transcript));
if (files.length === 0) process.exit(0);

const found = [];
for (const file of files) {
  let lines = [];
  try {
    lines = fs.readFileSync(file, 'utf8').split('\n');
  } catch {
    continue;
  }
  lines.forEach((line, i) => {
    if (!line.includes('console.log')) return;
    if (line.includes('keep-console')) return;
    found.push(`  ${file}:${i + 1}\n`);
  });
}

if (found.length) {
  process.stdout.write(`console.log в правленных за сессию файлах:\n${found.join('')}Убери или замени логгером; осознанный лог — пометь комментарием // keep-console.\n`);
}
