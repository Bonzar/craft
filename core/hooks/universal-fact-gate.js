#!/usr/bin/env node
// PreToolUse факт-гейт на ДЕСТРУКТИВНЫХ операциях. Самопроверка «я уверен»
// перед деструктивом не работает — гейт останавливает принудительно и требует
// предъявить факты в чате; ПОВТОРНЫЙ идентичный вызов проходит: повтор после
// предъявленных фактов и есть осознанное подтверждение.
//
// Одна копия, две ветки по каноническому действию. Командный список — рекурсивное удаление,
// жёсткий сброс и чистка в гите, силовая отправка, разрушающие подкоманды arc;
// список стартовый и пополняется уроками. То, что уже покрыто локальными
// гвардами с вопросом человеку, здесь не дублируется: вопрос сильнее отказа.
// Запись в Craft — удаление блоков и документов, перестройка схемы коллекции
// всегда; перенос блоков — только вне автономного прогона, там это белый список
// уборки.
//
// Автономный режим гейт НЕ байпасит (деструктив в автономе опаснее); байпас —
// только явный выключатель.
//
// Данные — не команды: хвост heredoc отрезается, кавычные строки вырезаются, а
// составные пары ищутся внутри ОДНОГО сегмента, а не по всей строке. Иначе текст
// в сообщении коммита читается как флаг.
//
// Fail open на всём неожиданном.
import fs from 'node:fs';
import path from 'node:path';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { sha256 } from './lib/hash.js';
import { factGateStateDir, sessionId } from './lib/paths.js';

if (process.env.FACT_GATE === 'off') process.exit(0);

const { raw, event, route, input } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

const isDataMutation = route === 'data.mutate';
if (!isDataMutation && route !== 'command.run') process.exit(0);

const command = input.command || '';
if (!command) process.exit(0);

const FORCE = /(^|\s)(--force|-f)(\s|$)/;
const DELETE_FLAG = /(^|\s)-D(\s|$)/;
const DESTRUCTIVE = [
  // Рекурсивное удаление: короткие сцепленные флаги в любом порядке или длинные.
  /(^|\s)rm\s+((-[a-z0-9]*[rR][a-z0-9]*f)|(-[a-z0-9]*f[a-z0-9]*[rR])|(--recursive\s.*--force)|(--force\s.*--recursive))/i,
  /(^|\s)git\s+((reset\s.*--hard)|(clean\s+-[a-z0-9]*f))/i,
  /(^|\s)arc\s+((clean(\s|$))|(checkout\s.*--force)|(stash\s+(drop|clear))|(unmount\s.*--forget))/i,
];
const GIT_PUSH = /(^|\s)git\s+push(\s|$)/;
const ARC_BRANCH = /(^|\s)arc\s+branch(\s|$)/;

let hit = '';
if (isDataMutation) {
  if (/(blocks delete|documents delete|collections schema-update)/.test(command)) hit = 'craft';
  else if (!process.env.CRAFT_AUTONOMOUS && /blocks move/.test(command)) hit = 'craft';
} else {
  const sanitized = command
    .split('<<')[0]
    .replace(/"[^"]*"/gs, '')
    .replace(/'[^']*'/gs, '');
  for (const segment of sanitized.split(/[;|&]/)) {
    if (!segment.trim()) continue;
    if (DESTRUCTIVE.some((re) => re.test(segment))
        || (GIT_PUSH.test(segment) && FORCE.test(segment))
        || (ARC_BRANCH.test(segment) && DELETE_FLAG.test(segment))) {
      hit = 'shell';
      break;
    }
  }
}
if (!hit) process.exit(0);

// Отказ один раз: маркер по хешу нормализованной команды. Повторный идентичный
// вызов проходит — факты предъявлены.
// Пробелы схлопываются вместе с завершающим переводом строки — так же, как это
// делала bash-версия через here-string: хвостовой перевод превращался в пробел и
// входил в хеш. Без него JS-версия дала бы другой маркер на ту же команду.
const normalized = `${command}\n`.replace(/[ \t\n\v\f\r]+/g, ' ');
const dir = factGateStateDir();
const marker = path.join(dir, `fact-gate.${sessionId() || 'default'}.${sha256(normalized)}`);
if (fs.existsSync(marker)) process.exit(0);
try {
  fs.writeFileSync(marker, '');
} catch { /* не записалось — в худшем случае гейт спросит второй раз */ }

// Счётчик полных текстов отказа за сессию: после трёх остаётся однострочник,
// иначе гейт раздувает контекст своими же объяснениями.
const counterFile = path.join(dir, `fact-gate.${sessionId() || 'default'}.count`);
let count = 0;
try {
  count = Number(fs.readFileSync(counterFile, 'utf8')) || 0;
} catch { /* счётчика ещё нет */ }
count += 1;
try {
  fs.writeFileSync(counterFile, String(count));
} catch { /* не записалось — счёт начнётся заново */ }

if (count > 3) {
  deny('Факт-гейт: предъяви факты и повтори вызов.');
}

if (hit === 'shell') {
  deny('Деструктивная команда остановлена факт-гейтом. Предъяви в чате факты: (1) что именно затронется — поимённый список; (2) однострочный откат; (3) дословная инструкция Влада или правило, разрешающее операцию. Предъявил — повтори ту же команду, второй вызов пройдёт.');
}
deny('Удаление в Craft остановлено факт-гейтом. Предъяви факты: (1) свежие бэклинки цели — кто ссылается и что сломается; (2) свежее чтение положения блока (родитель, соседи); (3) чем операция предписана. Предъявил — повтори команду.');
