#!/usr/bin/env node
// Пульт реестра одобренного: им агент смотрит реестр и закрывает выполненное.
//
// Реестр живёт файлом в /tmp и агенту не виден, поэтому адреса задач взять
// больше неоткуда — без пульта закрывать нечем.
//
//   node tools/registry.mjs show [--bodies]
//   node tools/registry.mjs close Ц1.2 Ц1.3
//
// show по умолчанию печатает СПИСОК РАБОТЫ: незакрытые задачи и цели, у которых
// они есть, скелетом — имена с адресами, без тел и логов. Закрытое из него
// уходит: это список того, что делать, и он обязан коротеть по мере работы.
// Тела — по --bodies, закрытое — по --all; вместе они дают полный вид, тот же,
// что уходит в сверку правки.
//
// close закрывает по адресу; тело и адреса задачи остаются в файле историей
// сессии, а в текст для модели закрытая задача идёт одним именем. Цель при этом
// не хоронится: работа под ней может продолжиться, и новая задача вернёт её в
// список. Неизвестный адрес — строка и ненулевой код, а не молчание: закрытие,
// которое не случилось, обязано быть видно.
//
// Расширение .mjs обязательно: в tools/ нет манифеста модулей, и .js читался бы
// как обычный скрипт, которому импорт недоступен.
import { approvalRegistry } from '../.claude/hooks/lib/paths.js';
import { readRegistry, render, closeTasks } from '../.claude/hooks/lib/registry.js';

const [, , command, ...rest] = process.argv;

function show() {
  const all = readRegistry(approvalRegistry());
  if (!all.length) {
    process.stdout.write('Реестр пуст: одобренного нет.\n');
    return 0;
  }
  const goals = rest.includes('--all') ? all : working(all);
  if (!goals.length) {
    process.stdout.write('Работы не осталось: всё одобренное закрыто.\n');
    return 0;
  }
  process.stdout.write(`${render(goals, { bodies: rest.includes('--bodies') })}\n`);
  return 0;
}

// Живая часть реестра: у целей остаются только незакрытые задачи, а цель, где
// не осталось ни одной, из списка уходит. Отдельного состояния для этого нет —
// «в работе» и есть наличие открытой задачи. Нумерация при этом СОХРАНЯЕТСЯ — и
// номер цели, и номер задачи: по ним закрывают, и сдвиг адресовал бы закрытие на
// чужую работу.
function working(goals) {
  return goals
    .map((goal, i) => ({ goal, n: i + 1 }))
    .map(({ goal, n }) => ({
      ...goal,
      n,
      tasks: (goal.tasks || []).filter((t) => t.state !== 'closed'),
    }))
    .filter((goal) => goal.tasks.length);
}

function close() {
  const addresses = rest.filter((a) => !a.startsWith('--'));
  if (!addresses.length) {
    process.stderr.write('Нечего закрывать: нужен адрес вида Ц1.2.\n');
    return 1;
  }
  const done = closeTasks(approvalRegistry(), addresses);
  if (done.closed.length) process.stdout.write(`Закрыто: ${done.closed.join(', ')}\n`);
  if (done.unknown.length) {
    process.stderr.write(`Не найдено в реестре: ${done.unknown.join(', ')}\n`);
    return 1;
  }
  return 0;
}

const registry = approvalRegistry();
if (!registry) {
  process.stderr.write('Реестра нет: сессия не опознана.\n');
  process.exit(1);
}

if (command === 'show') process.exit(show());
if (command === 'close') process.exit(close());

process.stderr.write('Команды: show [--bodies] [--all] | close Ц1.2 [Ц1.3 …]\n');
process.exit(1);
