// Портировано из google-gemini/gemini-cli, packages/core/src/utils/shell-utils.ts
//
// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0
//
// ИЗМЕНЕНИЯ ОТ ОРИГИНАЛА (требование Apache-2.0 §4b):
//   1. TypeScript переписан в JS без типов; экспорт ESM вместо TS-модуля.
//   2. Из detectCommandSubstitution убрана ветка PowerShell — у нас её нет.
//   3. Из stripShellWrapper убраны ветки cmd.exe и powershell по той же причине,
//      а разбор кавычек через shell-quote заменён простым снятием внешней пары:
//      разбором занимается наш собственный слой, и двойная работа тут не нужна.
//   4. Добавлена extractSubstitutions — её в оригинале нет. Оригинал отвечает
//      «есть ли подстановка», нам же нужно её СОДЕРЖИМОЕ: подстановка сама по
//      себе не запрет, судится то, что внутри неё.
//
// Версия источника: снимок ветки main от 26.08.2026, см. .claude/VENDORED-SKILLS.md.

// Снимает обёртку запуска через оболочку: `bash -c "…"` → `…`.
export function stripShellWrapper(command) {
  const pattern = /^\s*(?:(?:\S+\/)?(?:sh|bash|zsh|dash|ksh|fish))\s+-c\s+/i;
  const match = command.match(pattern);
  if (!match) return command.trim();
  let newCommand = command.substring(match[0].length).trim();
  if (
    newCommand.length >= 2
    && ((newCommand.startsWith('"') && newCommand.endsWith('"'))
      || (newCommand.startsWith("'") && newCommand.endsWith("'")))
  ) {
    newCommand = newCommand.substring(1, newCommand.length - 1);
  }
  return newCommand;
}

// Есть ли в команде подстановка, с учётом правил кавычек bash: в одинарных
// кавычках всё буквально, в двойных экранирование гасит доллар и обратную
// кавычку, вне кавычек работают и `$(`, и `<(`, и `>(`.
export function detectBashSubstitution(command) {
  let inSingleQuote = false;
  let inDoubleQuote = false;
  let i = 0;
  while (i < command.length) {
    const char = command[i];
    if (char === "'" && !inDoubleQuote) {
      inSingleQuote = !inSingleQuote;
      i += 1;
      continue;
    }
    if (char === '"' && !inSingleQuote) {
      inDoubleQuote = !inDoubleQuote;
      i += 1;
      continue;
    }
    if (inSingleQuote) {
      i += 1;
      continue;
    }
    if (char === '\\' && i + 1 < command.length) {
      if (inDoubleQuote) {
        const next = command[i + 1];
        if (['$', '`', '"', '\\', '\n'].includes(next)) {
          i += 2;
          continue;
        }
      } else {
        i += 2;
        continue;
      }
    }
    if (char === '$' && command[i + 1] === '(') return true;
    if (!inDoubleQuote && (char === '<' || char === '>') && command[i + 1] === '(') return true;
    if (char === '`') return true;
    i += 1;
  }
  return false;
}

// НАШЕ ДОПОЛНЕНИЕ. Содержимое подстановок — сами команды, и судить их надо тем
// же ходом. Обход тот же посимвольный, с теми же правилами кавычек; вложенные
// скобки считаются, чтобы `$(cat $(cat x))` вернулся одним куском целиком.
export function extractSubstitutions(command) {
  const found = [];
  let inSingleQuote = false;
  let i = 0;
  while (i < command.length) {
    const char = command[i];
    if (char === "'") {
      inSingleQuote = !inSingleQuote;
      i += 1;
      continue;
    }
    if (inSingleQuote) {
      i += 1;
      continue;
    }
    if (char === '\\' && i + 1 < command.length) {
      i += 2;
      continue;
    }
    const opensParen = (char === '$' || char === '<' || char === '>') && command[i + 1] === '(';
    if (opensParen) {
      let depth = 1;
      let j = i + 2;
      while (j < command.length && depth > 0) {
        if (command[j] === '(') depth += 1;
        else if (command[j] === ')') depth -= 1;
        j += 1;
      }
      // Скобка не закрылась — это уже не разбор, а сломанная строка: пусть
      // решает вызывающий, тут возвращать нечего.
      if (depth !== 0) return { substitutions: found, broken: true };
      found.push(command.slice(i + 2, j - 1));
      i = j;
      continue;
    }
    if (char === '`') {
      const end = command.indexOf('`', i + 1);
      if (end === -1) return { substitutions: found, broken: true };
      found.push(command.slice(i + 1, end));
      i = end + 1;
      continue;
    }
    i += 1;
  }
  return { substitutions: found, broken: false };
}
