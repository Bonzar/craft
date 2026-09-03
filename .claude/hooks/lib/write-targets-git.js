// Разбор вызовов git в команде: адаптер под инструмент. Общая часть спрашивает
// «меняет ли команда мир» и «есть ли здесь отправка», а какими словами это
// делает git, знает только этот файл.
//
// gitInvocations(команда) → [{sub, rest, flags}] на каждый вызов git.
// gitMutates(команда) → меняет ли хоть один вызов репозиторий.
// looksLikePush(команда) → есть ли отправка (пробный прогон отправкой не
//   является).
//
// Разбор идёт по КУСКАМ между разделителями и по тексту БЕЗ КАВЫЧЕК: слово в
// кавычках командой не является, а в цепочке правка стоит не только первой.
import { stripQuoted, stripQuotedHeredocs } from './write-targets-bash.js';

// Что стоит ПЕРЕД git и вызова не отменяет: присваивания окружения и обёртки
// запуска. Всё прочее впереди значит, что слово git — аргумент чужой команды
// («echo git push»), а не вызов.
const GIT_WRAPPERS = new Set(['sudo', 'env', 'command', 'time', 'nice', 'ionice', 'nohup', 'stdbuf']);

// Глобальные ключи git, которые ЗАБИРАЮТ значение следующим словом: без этого
// «git -C /repo push» читалось бы как подкоманда /repo.
const GIT_GLOBAL_WITH_VALUE = new Set(['-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path']);

// Все вызовы git в команде: {sub, rest, flags} на каждый.
//
// Разбор идёт по КУСКАМ между разделителями и по тексту БЕЗ КАВЫЧЕК. Иначе
// «printf 'git push'» считался бы пушем (слово в кавычках — не команда), а в
// «git status && git commit -m x» виден был бы только первый вызов, и правка
// цепочкой выглядела бы как ход без единого изменения.
export function gitInvocations(command) {
  const scan = stripQuoted(stripQuotedHeredocs(String(command || '')));
  const out = [];
  for (const piece of scan.split(/(?:\|\||&&|[;|\n])/)) {
    const parsed = gitParts(piece);
    if (parsed.sub) out.push(parsed);
  }
  return out;
}


// Подкоманда, слова за ней и её ключи. Ключи нужны отдельно: у части подкоманд
// именно ключ отличает перечисление от правки (`git tag --list 'v*'`).
function gitParts(piece) {
  const words = piece.trim().split(/\s+/).filter(Boolean);
  let i = 0;
  // Голова куска: присваивания и обёртки пропускаются, на всём остальном разбор
  // прекращается — git дальше уже не вызов, а аргумент.
  while (i < words.length && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]) || GIT_WRAPPERS.has(words[i]))) i += 1;
  const head = words[i] || '';
  if (head !== 'git' && !head.endsWith('/git')) return { sub: '', rest: [], flags: [] };
  let sub = '';
  i += 1;
  for (; i < words.length; i += 1) {
    const word = words[i];
    if (GIT_GLOBAL_WITH_VALUE.has(word)) { i += 1; continue; }
    if (word.startsWith('-')) continue;
    sub = word;
    i += 1;
    break;
  }
  const tail = words.slice(i);
  return { sub, rest: tail.filter((w) => !w.startsWith('-')), flags: tail.filter((w) => w.startsWith('-')) };
}

// Подкоманды гита, которые меняют репозиторий или рабочее дерево. Сетевые
// fetch и pull здесь же: они пишут ссылки и объекты в локальный репозиторий.
export const GIT_MUTATIONS = new Set([
  'push', 'commit', 'merge', 'rebase', 'reset', 'checkout', 'switch', 'restore',
  'stash', 'tag', 'cherry-pick', 'am', 'apply', 'revert', 'clean', 'rm', 'mv', 'add',
  'fetch', 'pull', 'branch', 'remote', 'worktree', 'init', 'clone',
]);

// У части мутирующих подкоманд есть ЧИТАЮЩИЕ формы, и решают их следующее слово
// или ключ: `git stash list`, `git remote show`, `git tag --list 'v*'` и
// `git branch -a` ничего не меняют. Ключ приходится смотреть отдельно от слов:
// у `git tag -l 'v*'` образец стоит обычным аргументом, и по одному его наличию
// перечисление выглядело бы как заведение метки.
const GIT_READING_SUBVERBS = new Set(['list', 'show']);
const GIT_READING_REMOTE = new Set(['show', 'get-url']);
const GIT_LISTING_FLAGS = new Set([
  '-l', '--list', '-n', '--contains', '--no-contains', '--points-at',
  '--merged', '--no-merged', '--sort', '--format', '-a', '--all', '-r', '--remotes',
]);

const listing = (flags) => flags.some((f) => GIT_LISTING_FLAGS.has(f.split('=')[0]));

function mutatingInvocation({ sub, rest, flags }) {
  if (!sub || !GIT_MUTATIONS.has(sub)) return false;
  if (sub === 'stash') return rest.length === 0 || !GIT_READING_SUBVERBS.has(rest[0]);
  if (sub === 'remote') return rest.length > 0 && !GIT_READING_REMOTE.has(rest[0]);
  if (sub === 'worktree') return rest.length > 0 && !GIT_READING_SUBVERBS.has(rest[0]);
  if (sub === 'tag' || sub === 'branch') return !listing(flags) && rest.length > 0;
  return true;
}

// Меняет ли команда репозиторий. Смотрятся ВСЕ вызовы в цепочке: у
// «git status && git commit -m x» правка стоит вторым, и по первому вызову ход
// выглядел бы как ход без единого изменения.
export function gitMutates(command) {
  return gitInvocations(command).some(mutatingInvocation);
}

// Пуш опознаётся по ВЫЗОВУ git и его подкоманде, а не по слову где угодно в
// строке: `git stash push`, `git commit -m "fix push"`, `git log --grep push`
// и `printf 'git push'` пушем не являются. Пробный прогон (--dry-run) тоже.
export function looksLikePush(command) {
  return gitInvocations(command)
    .some((inv) => inv.sub === 'push' && !inv.flags.includes('--dry-run'));
}
