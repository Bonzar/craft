// Метка репозитория сессии под git: адаптер под инструмент. Общая часть метрик
// получает готовую строку и про remote ничего не знает.
//
// repoOf(каталог) → «host/owner/repo» без схемы, учётки и .git; не репозиторий
// или нет remote — пустая строка, и метка сводки остаётся пустой.
// isIgnored(путь) → игнорирует ли репозиторий этот путь. Общая часть про
//   правила игнорирования не знает и получает готовый ответ.
// commonDir(корень) → общий git-каталог чекаута: у воркри он один на все,
//   поэтому файлы, обязанные пережить смену воркри, кладутся рядом с ним.
//
// Работа идёт вызовами самого гита, а не разбором его файлов: правила игнора
// живут в конфигурации репозитория, и любой свой разбор .gitignore рано или
// поздно разойдётся с тем, что считает игнором сам гит.
import { spawnSync } from 'node:child_process';

// Репо сессии по remote origin: host/owner/repo без схемы, учётки и .git.
// Не репозиторий, нет remote — пустая строка.
export function repoOf(cwd) {
  if (!cwd) return '';
  const res = spawnSync('git', ['-C', cwd, 'config', '--get', 'remote.origin.url'], {
    encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'],
  });
  if (res.status !== 0) return '';
  return normalizeRemote((res.stdout || '').trim());
}

export function normalizeRemote(url) {
  let s = String(url || '').trim();
  if (!s) return '';
  const hadScheme = /^[a-z+]+:\/\//i.test(s);
  s = s.replace(/^[a-z+]+:\/\//i, '');       // схема
  s = s.replace(/^[^@/]+@/, '');            // учётка перед хостом
  // scp-форма host:owner/repo — только там, где схемы НЕ было: с ней двоеточие
  // отделяет порт, и «github.com:443/a/b» превращалось в «github.com/443/a/b»,
  // то есть выдуманный владелец у каждого self-hosted remote на своём порту.
  if (!hadScheme) s = s.replace(/^([^:/]+):(?!\/)/, '$1/');
  else s = s.replace(/^([^:/]+):\d+\//, '$1/'); // порт из адреса выбрасывается
  s = s.replace(/\.git$/, '').replace(/\/+$/, '');
  return s;
}

// Ответы на «игнорируется ли путь» в пределах процесса. Один вызов хука
// спрашивает про один и тот же путь до трёх раз (гейт, якорь сессии, метрики), а
// у команды с несколькими целями это столько же форков на цель. Память живёт
// ровно столько, сколько процесс хука: за его жизнь игнор не меняется.
const ignoredCache = new Map();

// Игнорируется ли путь. Гит не найден, каталог не репозиторий, путь не под
// контролем — false: неизвестность трактуется как «не игнорируется», то есть в
// пользу проверки, а не в пользу пропуска.
export function isIgnored(file, cwd = process.cwd()) {
  const key = `${cwd}\u0000${file}`;
  if (ignoredCache.has(key)) return ignoredCache.get(key);
  const res = spawnSync('git', ['check-ignore', '-q', '--', file], { cwd, stdio: 'ignore' });
  const ignored = res.status === 0;
  ignoredCache.set(key, ignored);
  return ignored;
}

// Общий git-каталог чекаута: у воркри он один на все, поэтому файлы, которые
// обязаны пережить смену воркри (локальный .env, очередь метрик), кладутся
// рядом с ним. Не репозиторий или нет гита — пустая строка.
export function commonDir(root) {
  const res = spawnSync('git', ['-C', root, 'rev-parse', '--path-format=absolute', '--git-common-dir'], {
    encoding: 'utf8',
  });
  return res.status === 0 ? (res.stdout || '').trim() : '';
}
