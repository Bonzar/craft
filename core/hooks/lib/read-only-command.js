// Доказано ли, что команда только читает.
//
// ЗАЧЕМ ИМЕННО ТАК. Прежний гвард спрашивал «куда команда пишет» и искал цели по
// списку шаблонов записи. Такой список разрешает по умолчанию: чего в нём нет —
// проходит, а своя команда записи есть у любого стороннего инструмента. Здесь
// вопрос обратный, и умолчание тоже обратное: недоказанное закрыто.
//
// ЧТО СЧИТАЕТСЯ ДОКАЗАТЕЛЬСТВОМ — данные в vendor/read-only-rules.json. Там
// отобранные под этот предикат команды, их запрещённые флаги и read-only
// подкоманды инструментов.
//
// ЧЕМУ НАУЧИЛИ ЧУЖИЕ ПОЛОМКИ:
//   — проверяется КАЖДОЕ звено цепочки, а не первое: приписка через && обходила
//     гварды, смотревшие только на начало строки;
//   — имя, заданное путём, доказательством не считается: подложенный рядом
//     бинарник с именем известной утилиты обходил сверку по имени файла;
//   — обёртки снимаются: запуск через оболочку и ограничитель времени прятали
//     внутреннюю команду;
//   — содержимое подстановки судится как отдельная команда: запрещать её целиком
//     нельзя — соседние гварды сами предписывают рецепты с подстановкой.
//
// НЕИЗВЕСТНОСТЬ — НЕ РАЗРЕШЕНИЕ. Сломался разбор, не читаются правила, встретилась
// незнакомая конструкция — вердикт «не доказано», а не проход.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import parse from './vendor/shell-quote/parse.js';
import { stripShellWrapper, extractSubstitutions } from './vendor/gemini-shell-guards.js';

const RULES_PATH = process.env.READ_ONLY_RULES
  || path.join(path.dirname(fileURLToPath(import.meta.url)), 'vendor', 'read-only-rules.json');

let rulesCache;
function rules() {
  if (rulesCache !== undefined) return rulesCache;
  try {
    rulesCache = JSON.parse(fs.readFileSync(RULES_PATH, 'utf8'));
  } catch {
    // Правила не читаются — это не повод разрешить всё. Молчать тоже нельзя:
    // без строки в служебном выводе поломка выглядит как «гвард придирается».
    process.stderr.write('[read-only-command] правила не прочитаны — команды считаются недоказанными\n');
    rulesCache = null;
  }
  return rulesCache;
}

const verdict = (readOnly, cause, offender, source = null) => ({ readOnly, cause, offender, source });

function strongest(verdicts) {
  return verdicts.find((item) => item.cause === 'unparsed')
    || verdicts.find((item) => item.cause === 'unproven')
    || verdicts.find((item) => item.cause === 'mutates')
    || verdict(true, null, null);
}

// Операторы, по которым строка распадается на самостоятельные команды. Пайп в
// этом же ряду: звено пайпа — такая же команда, и `cat a | tee out` пишет.
const CHAIN_OPS = new Set(['&&', '||', ';', '|', '&', '|&']);

// Токены команды: shell-quote отдаёт строки и объекты-операторы. Перенаправления
// приходят объектами {op:'>'} — цель следующим токеном.
function splitChain(tokens) {
  const commands = [];
  let current = [];
  for (const token of tokens) {
    if (token && typeof token === 'object' && CHAIN_OPS.has(token.op)) {
      if (current.length) commands.push(current);
      current = [];
      continue;
    }
    current.push(token);
  }
  if (current.length) commands.push(current);
  return commands;
}

// Перенаправление в файл — запись; отвод в пустое устройство и дескрипторы —
// нет, иначе половина обычных читающих вызовов получала бы отказ.
function redirectsToFile(tokens, cfg) {
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (!token || typeof token !== 'object') continue;
    if (token.op !== '>' && token.op !== '>>') continue;
    const target = tokens[i + 1];
    if (typeof target !== 'string') return true;
    if (cfg.nullSinks.includes(target)) continue;
    return true;
  }
  return false;
}

function withoutRedirects(tokens) {
  const out = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token && typeof token === 'object' && ['>', '>>', '<'].includes(token.op)) {
      i += 1;
      continue;
    }
    out.push(token);
  }
  return out;
}

// Слова команды без операторов и без целей перенаправления.
function words(tokens) {
  const out = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token && typeof token === 'object') {
      if (token.op === '>' || token.op === '>>' || token.op === '<') i += 1;
      continue;
    }
    out.push(token);
  }
  return out;
}

// Префикс переменных окружения: `LC_ALL=C.UTF-8 grep …` — команда здесь grep.
function dropEnvPrefix(list) {
  let i = 0;
  while (i < list.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(list[i])) i += 1;
  return list.slice(i);
}

// Обёртки, у которых команда идёт дальше по строке. Флаги обёртки пропускаются
// вместе с их значениями настолько, насколько это видно без её собственной
// грамматики: неизвестное всё равно упрётся в «не доказано».
function dropWrappers(list, cfg) {
  let current = list;
  for (let guard = 0; guard < 4; guard += 1) {
    const head = current[0];
    if (!head || !cfg.wrappers.includes(head)) return current;
    let i = 1;
    while (i < current.length && current[i].startsWith('-')) i += 1;
    // У ограничителя времени первым идёт срок — не команда.
    if (head === 'timeout' && i < current.length && /^[0-9]/.test(current[i])) i += 1;
    current = current.slice(i);
  }
  return current;
}

function flagsOf(list) {
  return list.filter((word) => typeof word === 'string' && word.startsWith('-'));
}

// Одна простая команда: доказано чтение или нет.
function judgeSimple(tokens, cfg, redirectHandled = false) {
  if (!redirectHandled && redirectsToFile(tokens, cfg)) {
    const base = judgeSimple(withoutRedirects(tokens), cfg, true);
    // A redirect proves only its own file write. If the executable itself is
    // mutating or unproven, sending stdout to a bounded path cannot make that
    // separate side effect bounded.
    if (!base.readOnly) return base;
    const name = words(tokens)[0] || 'команда';
    return verdict(false, 'mutates', name, 'redirect');
  }

  const list = dropWrappers(dropEnvPrefix(words(tokens)), cfg);
  const name = list[0];
  if (!name) return verdict(true, null, null);

  // Имя, заданное путём, доказательством не считается: рядом с рабочим каталогом
  // можно положить свой бинарник и назвать его как известную утилиту.
  if (name.includes('/')) return verdict(false, 'unproven', name);

  const spec = cfg.readOnly[name];
  const nested = cfg.readOnlyNested[name];
  const subs = cfg.readOnlySubcommands[name];

  // Заведомо меняющие состояние названы отдельно ради текста отказа: ждать такую
  // команду незачем, её надо не переформулировать, а отложить до якоря.
  const mutating = cfg.mutating || [];
  const mutatingSubs = (cfg.mutatingSubcommands || {})[name] || [];
  if (mutating.includes(name)) return verdict(false, 'mutates', name);
  if (mutatingSubs.length) {
    const sub = list.slice(1).find((word) => !word.startsWith('-'));
    if (sub && mutatingSubs.includes(sub)) return verdict(false, 'mutates', `${name} ${sub}`);
  }

  if (spec) {
    const denied = spec.denyFlags || [];
    const used = flagsOf(list);
    for (const flag of denied) {
      if (used.includes(flag) || list.includes(flag)) return verdict(false, 'mutates', name);
    }
    return verdict(true, null, null);
  }

  if (subs || nested) {
    const sub = list.slice(1).find((word) => !word.startsWith('-'));
    if (!sub) return verdict(false, 'unproven', name);
    if (nested && nested[sub]) {
      const third = list.slice(list.indexOf(sub) + 1).find((word) => word);
      if (third && nested[sub].includes(third)) return verdict(true, null, null);
      return verdict(false, 'unproven', `${name} ${sub}`);
    }
    if (subs && subs.includes(sub)) {
      const denied = (cfg.denySubcommandFlags[name] || {})[sub] || [];
      for (const flag of denied) {
        if (list.some((word) => word === flag || word.startsWith(`${flag}=`))) {
          return verdict(false, 'mutates', `${name} ${sub}`);
        }
      }
      return verdict(true, null, null);
    }
    return verdict(false, 'unproven', `${name} ${sub}`);
  }

  // Незнакомая утилита. Это не «безопасно» и не «опасно» — это «не доказано»,
  // и лечится оно ответом Влада, а не догадкой гварда.
  return verdict(false, 'unproven', name);
}

// Разбор одной строки: подстановки судятся первыми, потом сама строка.
function judgeStringVerdicts(command, cfg, depth) {
  if (depth > 3) return [verdict(false, 'unparsed', command.trim().split(/\s+/)[0] || 'команда')];

  const unwrapped = stripShellWrapper(command);

  const { substitutions, broken } = extractSubstitutions(unwrapped);
  if (broken) return [verdict(false, 'unparsed', unwrapped.trim().split(/\s+/)[0] || 'команда')];
  const verdicts = substitutions.flatMap((inner) => judgeStringVerdicts(inner, cfg, depth + 1));

  let tokens;
  try {
    tokens = parse(unwrapped);
  } catch {
    return [verdict(false, 'unparsed', unwrapped.trim().split(/\s+/)[0] || 'команда')];
  }
  // Незакрытая кавычка не бросает исключение — она возвращает хвост одним
  // куском. Считаем такую строку неразобранной: судить по ней нечего.
  if (/^[^']*'[^']*$/.test(unwrapped) || /^[^"]*"[^"]*$/.test(unwrapped)) {
    return [verdict(false, 'unparsed', unwrapped.trim().split(/\s+/)[0] || 'команда')];
  }
  if (tokens.some((token) => token && typeof token === 'object' && token.comment !== undefined)) {
    return [verdict(false, 'unparsed', unwrapped.trim().split(/\s+/)[0] || 'команда')];
  }

  for (const simple of splitChain(tokens)) {
    verdicts.push(judgeSimple(simple, cfg));
  }
  return verdicts;
}

function judgeString(command, cfg, depth) {
  return strongest(judgeStringVerdicts(command, cfg, depth));
}

// classifyCommand(строка) → { readOnly, cause, offender, source }.
//   cause: 'mutates'   — команда меняет состояние, ждать её незачем;
//          'unproven'  — про неё не видно, что она только читает;
//          'unparsed'  — строка не разобралась, судить нечем.
//   source: 'redirect' — мутацию создаёт только файловый redirect; null —
//                       причина относится к самой команде или неизвестна.
export function classifyCommand(command) {
  const cfg = rules();
  if (!cfg) return verdict(false, 'unproven', 'правила недоступны');
  if (typeof command !== 'string' || command.trim() === '') return verdict(true, null, null);
  return judgeString(command, cfg, 0);
}

// Подробный результат нужен гейту для составных команд: общий strongest-вердикт
// не различает `rm /tmp/x; rm /tmp/y` и `rm /tmp/x; publish`. В обоих случаях
// он говорит лишь «mutates», хотя только в первом каждая мутация имеет
// ограниченную файловую цель.
export function classifyCommandParts(command) {
  const cfg = rules();
  if (!cfg) return [verdict(false, 'unproven', 'правила недоступны')];
  if (typeof command !== 'string' || command.trim() === '') return [];
  return judgeStringVerdicts(command, cfg, 0);
}
