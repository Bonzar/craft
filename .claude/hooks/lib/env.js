// Загрузка `.env` в окружение хука. Харнесс этого не делает, поэтому без вызова
// отсюда доступ к connect-API остаётся незаданным, и сетевые хуки молча
// пропускают работу.
//
// Файл лежит в корне основного чекаута и в гит не попадает (в нём токен). Из
// воркри его не видно, поэтому вторым заходом путь берётся от общего git-каталога.
//
// Вне craft-репо `.env` нет вовсе: универсальные хуки, установленные в
// пользовательский слой, работают в произвольных сессиях и берут доступ из
// личного файла, который заводит install.sh. Он читается ТОЛЬКО когда доступ ещё
// не задан — окружение сессии всегда старше файла на диске.
//
// Разбор здесь простой (KEY=VALUE со снятием кавычек), а не исполнение файла
// оболочкой: в этих файлах живут присваивания, и запускать их кодом ради
// подстановок значило бы исполнять произвольный текст на каждом старте сессии.
import fs from 'node:fs';
import path from 'node:path';

function parse(text) {
  const out = {};
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (trimmed === '' || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).replace(/^export\s+/, '').trim();
    let value = trimmed.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (key) out[key] = value;
  }
  return out;
}

function apply(file, { onlyIfUnset = false } = {}) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return false;
  }
  for (const [key, value] of Object.entries(parse(text))) {
    // «Задано» — это НАЛИЧИЕ ключа, а не его непустота. Пустая строка ставится
    // намеренно («этого канала у меня нет»), и перебивать её файлом значит
    // отменять решение вызывающего: так фикстура, погасившая доступ к connect-API,
    // получала его обратно из личного файла и шла в сеть.
    if (onlyIfUnset && key in process.env) continue;
    process.env[key] = value;
  }
  return true;
}

// Корень чекаута считается от ЭТОГО модуля, а не от файла вызывающего хука: у
// них разная глубина (хук лежит на уровень выше), и общая формула на стороне
// вызова уводила бы поиск `.env` мимо чекаута — доступ к connect-API молча
// оставался бы незаданным.
//
// `commonDir` — АДАПТЕР рабочей копии: он приходит параметром с края, потому что
// «общий каталог рабочей копии» знает инструмент, а не общая часть. Адаптера нет
// — второго захода из воркри просто не будет, и это не тихий обход, а отсутствие
// возможности: файл ищется только по первому пути.
// `root` и `personalEnv` приходят от края (адаптер env-claude.js): корень проекта
// харнес задаёт своей переменной, а личный файл доступа лежит в его каталоге
// настроек — имён того и другого общая часть не знает. Не дали — считаем корень от
// самого модуля, а личный файл не читаем вовсе.
export function loadEnv({ commonDir, root: given = '', personalEnv = '' } = {}) {
  const self = new URL(import.meta.url).pathname;
  const root = given || path.resolve(path.dirname(self), '..', '..', '..');

  let envFile = path.join(root, '.env');
  if (!fs.existsSync(envFile) && typeof commonDir === 'function') {
    const common = commonDir(root);
    if (common) envFile = path.join(path.dirname(common), '.env');
  }
  apply(envFile);

  // Личный файл доступа читается, только когда доступа НЕТ ВОВСЕ. Пустая строка —
  // это тоже ответ («не ходи в сеть»), поэтому спрашивается наличие ключа.
  if (!('CRAFT_API_BASE' in process.env) && personalEnv) {
    apply(personalEnv, { onlyIfUnset: true });
  }
}
