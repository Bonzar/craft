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

const HOOKS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '.claude', 'hooks');

function sources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return sources(full);
    return e.isFile() && full.endsWith('.js') ? [full] : [];
  });
}

// Ищется не ИМЯ файла, а сам поступок: запись на диск, в которую отдают событие,
// его сырой текст или вход вызова. Проверка по именам ловила только те следы,
// что назвали себя `last-input`, — след под любым другим именем проходил.
const WRITE = /\b(?:appendFileSync|writeFileSync|createWriteStream|writeFile|appendFile)\s*\(/;

// Подозрительна ТОЛЬКО начинка записи, а не её адрес: временный файл с именем
// `input` — обычное дело, а вот отданное в него событие или вход вызова — нет.
const PAYLOAD = /\b(raw|event|tool_input|tool_response)\b|JSON\.stringify\(\s*input\b|\$\{\s*input\b|\binput\s*[,)]/;

export function tracesInput(text) {
  const guilty = [];
  for (const line of text.split('\n')) {
    const at = line.search(WRITE);
    if (at < 0) continue;
    const args = line.slice(at + line.match(WRITE)[0].length);
    const comma = args.indexOf(',');
    if (comma < 0) continue; // запись без начинки — нечего отдавать
    if (PAYLOAD.test(args.slice(comma + 1))) guilty.push(line.trim());
  }
  return guilty;
}

test('хуки не пишут на диск событие и вход вызова', () => {
  const guilty = [];
  for (const file of sources(HOOKS)) {
    for (const line of tracesInput(fs.readFileSync(file, 'utf8'))) {
      guilty.push(`${path.relative(HOOKS, file)}: ${line}`);
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
