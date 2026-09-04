// Гвард: хуки не пишут на диск СЛЕД ВХОДА — снимок события с текстом правки,
// командой или промптом. Такой файл был у план-гейта и кнопки; он лежал в общем
// /tmp, читался кем угодно на машине и снят в фазе 0.
//
// Проверка идёт по ИСХОДНИКАМ, а не по отсутствию файла с зашитым путём: кейс,
// который смотрит на конкретный путь, зеленеет и когда след вернулся под другим
// именем, — то есть проверяет ровно ничто.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

// Смотрятся ВСЕ исполняемые исходники слоя, а не только `.js` под хуками:
// приём реестра лежит в `tools/` и пишет на диск ровно так же.
const SCANNED = [path.join(ROOT, '.claude', 'hooks'), path.join(ROOT, 'tools')];

function sources(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return sources(full);
    return e.isFile() && /\.m?js$/.test(full) ? [full] : [];
  });
}

// Ищется не ИМЯ файла, а сам поступок: запись на диск, в которую отдают событие,
// его сырой текст или вход вызова. Проверка по именам ловила только те следы,
// что назвали себя `last-input`, — след под любым другим именем проходил.
const WRITE = /\b(?:appendFileSync|writeFileSync|createWriteStream|writeFile|appendFile)\s*\(/g;

// Подозрительна ТОЛЬКО начинка записи, а не её адрес: временный файл с именем
// `input` — обычное дело, а вот отданное в него событие или вход вызова — нет.
const PAYLOAD = /\b(raw|event|tool_input|tool_response)\b|JSON\.stringify\(\s*input\b|\$\{\s*input\b|\binput\s*[,)]/;

// Начинку часто считают СТРОКОЙ ВЫШЕ: `const body = JSON.stringify(event)`, а
// пишут уже `body`. По одному вызову такой след не виден, поэтому имена, в
// которые положили событие или вход, собираются заранее и дальше считаются
// такой же начинкой.
const CARRIER = /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([^;\n]*)/g;
const CARRIED = /JSON\.stringify\(\s*(raw|event|input|tool_input|tool_response)\b|^\s*(raw|event|tool_input|tool_response)\s*$|`[^`]*\$\{\s*(raw|event|input|tool_input|tool_response)\b/;

function carriers(text) {
  const names = [];
  for (const m of text.matchAll(CARRIER)) if (CARRIED.test(m[2])) names.push(m[1]);
  return names;
}

// Текст аргументов вызова: от открывающей скобки до ПАРНОЙ ей, через переводы
// строк. Построчный разбор терял вызов, разбитый переносом, — а перенос тут
// обычное дело, аргументов у записи бывает три.
function callArgs(text, from) {
  let depth = 0;
  for (let i = from; i < text.length; i += 1) {
    if (text[i] === '(') depth += 1;
    else if (text[i] === ')') {
      depth -= 1;
      if (depth === 0) return text.slice(from + 1, i);
    }
  }
  return text.slice(from + 1);
}

// Начинка — всё после ПЕРВОЙ запятой верхнего уровня: запятые внутри вложенных
// вызовов и объектов адрес от начинки не отделяют.
function payloadOf(args) {
  let depth = 0;
  for (let i = 0; i < args.length; i += 1) {
    const ch = args[i];
    if ('([{'.includes(ch)) depth += 1;
    else if (')]}'.includes(ch)) depth -= 1;
    else if (ch === ',' && depth === 0) return args.slice(i + 1);
  }
  return '';
}

export function tracesInput(text) {
  const carried = carriers(text);
  const guilty = [];
  for (const m of text.matchAll(WRITE)) {
    const open = m.index + m[0].length - 1;
    const payload = payloadOf(callArgs(text, open));
    if (!payload.trim()) continue; // запись без начинки — нечего отдавать
    const named = carried.some((name) => new RegExp(`\\b${name}\\b`).test(payload));
    if (named || PAYLOAD.test(payload)) guilty.push(`${m[0]}${payload.trim().slice(0, 80)}`);
  }
  return guilty;
}

test('хуки не пишут на диск событие и вход вызова', () => {
  const guilty = [];
  for (const dir of SCANNED) {
    for (const file of sources(dir)) {
      for (const line of tracesInput(fs.readFileSync(file, 'utf8'))) {
        guilty.push(`${path.relative(ROOT, file)}: ${line}`);
      }
    }
  }
  assert.deepEqual(guilty, [], 'ни одна запись на диск не отдаёт событие или вход вызова');
});

// Сам гвард обязан ловить: без этого «список пуст» ничего не значит.
test('гвард следа ловит запись входа под любым именем', () => {
  assert.deepEqual(tracesInput("fs.writeFileSync(somewhere, JSON.stringify(input));").length, 1);
  assert.deepEqual(tracesInput("fs.appendFileSync(f, `${raw}\\n`);").length, 1);
  assert.deepEqual(tracesInput("fs.writeFileSync(mark, '');"), [], 'обычная метка следом не является');
  assert.deepEqual(tracesInput('fs.writeFileSync(input, approved);'), [],
    'временный файл с именем input — адрес записи, а не её начинка');
});

// Две дыры построчного разбора: начинку считают строкой выше, а сам вызов
// разбивают переносом. И то и другое — тот же след, и гвард обязан его видеть.
test('след виден и через промежуточное имя, и через перенос строки', () => {
  assert.equal(tracesInput([
    "const body = JSON.stringify(event);",
    'fs.writeFileSync(f, body);',
  ].join('\n')).length, 1, 'начинку положили в имя строкой выше');
  assert.equal(tracesInput([
    'fs.appendFileSync(',
    '  f,',
    '  JSON.stringify(tool_input),',
    ');',
  ].join('\n')).length, 1, 'вызов разбит переносом');
  assert.deepEqual(tracesInput([
    "const body = JSON.stringify({ kind: 'pre' });",
    'fs.writeFileSync(f, body);',
  ].join('\n')), [], 'своя запись журнала следом не является');
});
