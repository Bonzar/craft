// Гит для гвардов. Работа идёт вызовами самого гита, а не библиотекой: правила
// игнора живут в конфигурации репозитория, и любой свой разбор .gitignore рано
// или поздно разойдётся с тем, что считает игнором сам гит.
import { spawnSync } from 'node:child_process';

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
