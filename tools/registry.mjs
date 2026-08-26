#!/usr/bin/env node
// Пульт реестра одобренного: им агент смотрит реестр и закрывает выполненное.
//
// Реестр живёт файлом в /tmp и агенту не виден, поэтому адреса задач взять
// больше неоткуда — без пульта закрывать нечем.
//
//   node tools/registry.mjs show [--bodies]
//   node tools/registry.mjs close Ц1.2 Ц1.3
//
// show по умолчанию печатает СКЕЛЕТ — цели и имена задач с адресами, без тел и
// логов: для выбора адреса тела не нужны, а полный рендер живого реестра тянет
// десятки тысяч символов в контекст. Полный вид — по --bodies, он же уходит в
// сверку правки.
//
// close закрывает по адресу и снимает у задачи тело: работа кончилась, держать
// её текст незачем. Цель, у которой закрылась последняя задача, уходит
// надгробием. Неизвестный адрес — строка и ненулевой код, а не молчание:
// закрытие, которое не случилось, обязано быть видно.
//
// Расширение .mjs обязательно: в tools/ нет манифеста модулей, и .js читался бы
// как обычный скрипт, которому импорт недоступен.
import { approvalRegistry } from '../.claude/hooks/lib/paths.js';
import { readRegistry, render, closeTasks } from '../.claude/hooks/lib/registry.js';

const [, , command, ...rest] = process.argv;

function show() {
  const goals = readRegistry(approvalRegistry());
  if (!goals.length) {
    process.stdout.write('Реестр пуст: одобренного нет.\n');
    return 0;
  }
  process.stdout.write(`${render(goals, { bodies: rest.includes('--bodies') })}\n`);
  return 0;
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

process.stderr.write('Команды: show [--bodies] | close Ц1.2 [Ц1.3 …]\n');
process.exit(1);
