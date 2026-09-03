// Гит для гвардов. Работа идёт вызовами самого гита, а не библиотекой: правила
// игнора живут в конфигурации репозитория, и любой свой разбор .gitignore рано
// или поздно разойдётся с тем, что считает игнором сам гит.
import { spawnSync } from 'node:child_process';

// Игнорируется ли путь. Гит не найден, каталог не репозиторий, путь не под
// контролем — false: неизвестность трактуется как «не игнорируется», то есть в
// пользу проверки, а не в пользу пропуска.
export function isIgnored(file, cwd = process.cwd()) {
  const res = spawnSync('git', ['check-ignore', '-q', '--', file], { cwd, stdio: 'ignore' });
  return res.status === 0;
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
