// Метка репозитория сессии под git: адаптер под инструмент. Общая часть метрик
// получает готовую строку и про remote ничего не знает.
//
// repoOf(каталог) → «host/owner/repo» без схемы, учётки и .git; не репозиторий
// или нет remote — пустая строка, и метка сводки остаётся пустой.
// isIgnored(путь) → игнорирует ли репозиторий этот путь. Общая часть про
//   правила игнорирования не знает и получает готовый ответ.
import { spawnSync } from 'node:child_process';

export { isIgnored } from './git.js';

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
