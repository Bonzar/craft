// Уступка второму вызову того же события.
//
// Хук зарегистрирован сразу в двух местах — в проектных настройках репозитория и
// в пользовательских, которые ставит install.sh. Оба вызова приходят на одно
// событие, и без дедупликации хук отрабатывает дважды: двойная директива в
// контексте, двойной отказ, двойной инкремент счётчиков.
//
// Уступка идёт в два шага, потому что двойная регистрация бывает двух видов.
//
// ШАГ 1 — по чекауту. Локально сессии режется отдельный воркри: проектная
// регистрация исполняет файл хука ИЗ него, а пользовательская — файл основного
// чекаута. Это разные файлы разных версий, и работать должен тот, чей файл лежит
// в чекауте сессии: его код и есть код этой сессии. Посторонний уступает.
// Чекаут сессии опознаётся по каталогу, который событие приносит с собой, а не
// по переменной окружения с корнем проекта: в окружении инструментов её нет, и
// правило, стоящее на ней, молча выключилось бы там, где её не задают.
//
// ШАГ 2 — по факту. Когда обе регистрации ведут в ОДИН файл, пути совпадают
// буквально и признака «кто из двух» не остаётся. Тогда решает занятие события:
// первый вызов ставит метку, второй видит занятое и выходит.
//
// Ключ метки — сессия, имя файла хука и само содержимое события. Содержимое
// обязательно: без него метка первого сообщения дожила бы до второго и погасила
// бы хук навсегда. Срок жизни метки — единицы секунд: два вызова одной
// регистрации приходят одновременно, а следующее сообщение с тем же текстом
// приходит позже этого срока.
//
// Имя в ключе берётся БЕЗ ЛЮБОГО расширения, а не только без `.sh`, как в
// bash-версии: пока слой переезжает, рядом лежат две версии одного хука, и по
// разным ключам они обе отработали бы на одно событие — ровно то, от чего этот
// механизм и написан.
import fs from 'node:fs';
import path from 'node:path';
import { sha256 } from './hash.js';
import { hookOnceDir } from './paths.js';

// true — в чекауте сессии лежит файл ЭТОГО же хука, а исполняется другой:
// значит вызов посторонний и уступает.
function yieldsToSessionCheckout(event, selfPath) {
  const cwd = event && typeof event.cwd === 'string' ? event.cwd : '';
  if (!cwd) return false;
  let probe;
  try {
    if (!fs.statSync(cwd).isDirectory()) return false;
    probe = fs.realpathSync(cwd);
  } catch {
    return false;
  }

  // Имя без расширения — по той же причине, что и ключ метки ниже: пока слой
  // переезжает, чекаут сессии может нести .sh там, где исполняется .js. Файл
  // чекаута и есть код этой сессии независимо от того, чем он написан.
  const base = path.basename(selfPath).replace(/\.[^.]+$/, '');
  const selfDir = path.dirname(selfPath);
  // Чекаут — ближайший каталог вверх от рабочего, у которого есть свой
  // core/hooks с файлом этого имени. Именно файл, а не сам факт репозитория:
  // воркри может быть старее основного чекаута и нужного хука ещё не содержать.
  // Сравниваются КАТАЛОГИ, а не пути файлов: у одноимённых файлов это одно и то
  // же, а при разных расширениях сравнение путей объявило бы посторонним свой же
  // экземпляр из того же каталога.
  while (probe && probe !== '/') {
    for (const hooks of [path.join(probe, 'core', 'hooks')]) {
      let names = [];
      try {
        names = fs.readdirSync(hooks);
      } catch { /* каталога хуков здесь нет */ }
      if (names.some((n) => n.replace(/\.[^.]+$/, '') === base)) {
        const selfReal = fs.existsSync(selfDir) ? fs.realpathSync(selfDir) : selfDir;
        return selfReal !== fs.realpathSync(hooks);
      }
    }
    probe = path.dirname(probe);
  }
  return false;
}

// true — работай; false — уступи (посторонний экземпляр либо занятое событие).
export function hookOnce(raw, event, moduleUrl) {
  // Выключатель гасит ОБА шага разом: иначе исход кейсов зависел бы от того, из
  // какого каталога запущен прогон тестов.
  if (process.env.HOOK_ONCE === 'off') return true;

  const selfPath = new URL(moduleUrl).pathname;

  // Blocking action hooks may only deduplicate calls that carry a stable
  // invocation identity. Without it, a second identical payload can be a real
  // retry rather than the second registration of one native call; running the
  // guard twice is safe, skipping a retry is not.
  if (event?.name === 'action.before' && !event.invocationId) return true;

  // A critical guard cannot yield merely because a checkout contains a file
  // with the same name: that does not prove the project registered or ran it.
  // Stable invocation identity below still deduplicates two real registrations.
  if (event?.name !== 'action.before' && yieldsToSessionCheckout(event, selfPath)) return false;

  const name = path.basename(selfPath).replace(/\.[^.]+$/, '');
  const key = sha256(raw ?? '');
  const mark = path.join(hookOnceDir(), `hook-once.${name}.${key}`);
  const ttl = Number(process.env.HOOK_ONCE_TTL || 5);

  // Создание каталога атомарно: из двух одновременных вызовов ровно один его
  // создаёт, второй получает отказ и уступает.
  try {
    fs.mkdirSync(mark);
    return true;
  } catch {
    // Метка уже есть — решает её возраст.
  }

  let age = Infinity;
  try {
    age = (Date.now() - fs.statSync(mark).mtimeMs) / 1000;
  } catch {
    return true;
  }
  if (age <= ttl) return false;

  // Метка протухла (то же сообщение повторили позже) — занимаем событие заново.
  try {
    const now = new Date();
    fs.utimesSync(mark, now, now);
  } catch { /* не вышло обновить метку — работаем всё равно */ }
  return true;
}
