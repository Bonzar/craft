#!/usr/bin/env node
// PreToolUse plan-gate: рабочие правки — код, система, Craft — по умолчанию
// закрыты. ЭТО ЯДРО: своих решений у него нет.
//
// Делает три вещи:
//   1. спрашивает инструмент признаков записи (lib/write-intent.js), что вызов
//      делает. Определение записи на весь контур одно, и оно там;
//   2. выходит, если вызов ничего не пишет;
//   3. гонит по вызову правила из списка (lib/gate-rules-table.js) — якорь,
//      предодобренная зона, рубильник, сверка с реестром одобренного. Первое
//      решение обрывает цепочку.
//
// Раньше все четыре решения жили прямо здесь вперемешку с разбором поверхностей,
// а пятое (якорь) стояло отдельным гвардом со СВОИМ определением записи: одна и
// та же команда у одного была записью, у другого нет.
//
// Выходов у ядра нет: ни автономного прогона, ни режима харнесса. Снять проверки
// можно только тапом Влада — рубильником, который живёт записью в реестре и
// гаснет со сменой сессии. Молчать может отдельное ПРАВИЛО (так делает якорь в
// автономной рутине), но сверка с реестром идёт наравне со всеми.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { lastInputTrace, approvalRegistry } from './lib/paths.js';
import { waitForParsing, readRegistry } from './lib/registry.js';
import { writeIntent, isCraftWrite } from './lib/write-intent.js';
import { RULES } from './lib/gate-rules-table.js';

const { raw, event, tool, input } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

// Отладочный след последнего входа (эфемерный): по нему проверяются факты о
// составе hook-входа (напр. поле permission_mode) без правки харнесса.
try {
  fs.writeFileSync(lastInputTrace('plan-gate'), raw);
} catch { /* след не записался — на решение гейта это не влияет */ }

const intent = writeIntent({ tool, input });
if (!intent.writes) process.exit(0);

// Предел описания правки. Число здесь — страховка от бинарного мусора (случайный
// дамп, картинка), а не бюджет: правка такого размера в промпт не помещается по
// смыслу, а не по форме. Прежние 2000/4000 байт стояли из-за потолка на аргумент
// командной строки и резали живую правку посреди кода — промпт давно идёт на
// стандартный ввод.
const DESC_LIMIT = 200000;

// Срез по БАЙТАМ с хвостовым переводом строки, как его делал bash: подстановка
// команды добавляла к тексту перевод строки, резала head -c и снимала хвостовые
// переводы обратно. Возвращается буфер — срез посреди многобайтного символа
// обязан дать те же байты, что давал шелл.
function headBytes(text, limit) {
  const cut = Buffer.from(`${text}\n`, 'utf8').subarray(0, limit);
  let end = cut.length;
  while (end > 0 && cut[end - 1] === 0x0a) end -= 1;
  return cut.subarray(0, end);
}

// Описание правки для сверки: что именно делается и с чем. Собирается по
// поверхности, потому что смысл правки виден из разного — у файла из текста, у
// команды из самой строки, у прочего инструмента из его входа.
function describeCall() {
  if (isCraftWrite(tool)) {
    return Buffer.concat([
      Buffer.from('инструмент: craft_write\nкоманда:\n', 'utf8'),
      headBytes(input.command || '', DESC_LIMIT),
    ]);
  }
  if (intent.kind === 'command') {
    return Buffer.concat([
      Buffer.from('инструмент: Bash\nкоманда:\n', 'utf8'),
      headBytes(input.command || '', DESC_LIMIT),
    ]);
  }
  if (intent.kind === 'file') {
    // Текст правки по инструменту: Edit/Write — new_string/content, MultiEdit —
    // все edits[].new_string, NotebookEdit — new_source; без них классификатор
    // видел бы пустую правку и не мог ловить выход за одобренное. Заменяемый
    // текст (old_string) сериализуется тоже: без него у Edit новый текст читается
    // как ДОБАВЛЕНИЕ целиком, и якорные строки замены дают ложное «сверх плана».
    const edits = Array.isArray(input.edits) ? input.edits : [];
    const joined = (key) => edits.map((e) => (e && e[key]) || '').join('\n---\n');
    const oldText = input.old_string ?? (joined('old_string') || '');
    const newText = input.new_string ?? input.content ?? input.new_source ?? (joined('new_string') || '');
    return Buffer.concat([
      Buffer.from(`инструмент: ${tool}\nфайл: ${input.file_path || input.notebook_path || ''}\nзаменяемый текст:\n`, 'utf8'),
      headBytes(oldText, DESC_LIMIT),
      Buffer.from('\nновый текст:\n', 'utf8'),
      headBytes(newText, DESC_LIMIT),
    ]);
  }
  return Buffer.concat([
    Buffer.from(`инструмент: ${tool}\nвход:\n`, 'utf8'),
    headBytes(JSON.stringify(input), DESC_LIMIT),
  ]);
}

// Реестр читается лениво и один раз на вызов: правилам его нужно двум, а разбор
// реплики идёт фоном — ждать его дважды значило бы удваивать задержку.
//
// Сверка ЖДЁТ конца разбора: правка, которую Влад только что разрешил репликой,
// иначе упёрлась бы в гейт, читающий ещё пустой реестр.
const registryFile = approvalRegistry();
let goals = null;
function registry() {
  if (goals === null) {
    waitForParsing(registryFile);
    goals = readRegistry(registryFile);
  }
  return goals;
}

let describedOnce = null;
const ctx = {
  tool,
  input,
  intent,
  raw,
  registryFile,
  registry,
  description() {
    if (describedOnce === null) describedOnce = describeCall();
    return describedOnce;
  },
};

for (const rule of RULES) {
  const verdict = rule.run(ctx) || { decision: 'next' };
  if (verdict.decision === 'allow') process.exit(0);
  if (verdict.decision === 'deny') deny(verdict.reason);
}

// Сюда попасть нельзя: сверка с реестром отвечает всегда. Но если список правил
// однажды окажется неполным, вызов не должен проходить молча — нет решения блок.
deny('Заблокировано план-гейтом: ни одно правило не дало решения, а без него правка не идёт.');
