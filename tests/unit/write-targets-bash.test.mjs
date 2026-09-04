// Доказательство чтения: гвард якоря спрашивает не «куда команда пишет», а
// «видно ли, что она только читает». Список записи разрешал по умолчанию —
// незнакомая утилита проходила молча, а своя команда записи есть у любого
// стороннего инструмента. Здесь закреплено поведение на границах: что считается
// доказанным чтением, что — недоказанным, и по какой причине.
//
// Расширение .mjs — по той же причине, что у соседей: в каталоге тестов нет
// манифеста модулей, и .js читался бы как обычный скрипт без импорта.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { classifyCommand } = await import('../../.claude/hooks/lib/write-targets-bash.js');

const readOnly = (cmd) => classifyCommand(cmd).readOnly;
const cause = (cmd) => classifyCommand(cmd).cause;

// --- то, что протекало сквозь список записи ---------------------------------
// Прогон гварда на живой сессии показал десять команд, проходивших мимо. Каждая
// названа поимённо: без этого возврат любой из них в проходящие не заметит никто.
test('команды, протекавшие сквозь список записи, чтением не считаются', () => {
  for (const cmd of [
    'rm README.md',
    'touch newfile.txt',
    'mkdir build',
    'chmod +x install.sh',
    'ln -sf a b',
    'truncate -s 0 README.md',
    'dd if=/dev/zero of=README.md',
    'git checkout -- README.md',
    'git clean -fd',
    'npm install',
  ]) {
    assert.equal(readOnly(cmd), false, cmd);
  }
});

test('чтение доказывается и проходит', () => {
  for (const cmd of [
    'cat README.md',
    'ls -la',
    'grep -rn "якорь" .claude/hooks',
    'rg --files',
    'head -20 tests/run.js',
    'wc -l README.md',
    'git status',
    'git log --oneline -5',
    'git diff HEAD~1',
  ]) {
    assert.equal(readOnly(cmd), true, cmd);
  }
});

// --- цепочка ----------------------------------------------------------------
// Проверяется КАЖДОЕ звено: гвард, смотрящий только на первую команду, обходится
// припиской через && — ровно так обходили правила в соседних инструментах.
test('в цепочке проверяется каждое звено, а не первое', () => {
  assert.equal(readOnly('cat a.txt && cat b.txt'), true);
  assert.equal(readOnly('cat a.txt && rm b.txt'), false);
  assert.equal(readOnly('cat a.txt; rm b.txt'), false);
  assert.equal(readOnly('cat a.txt || rm b.txt'), false);
  assert.equal(readOnly('cat a.txt | grep x | wc -l'), true);
  assert.equal(readOnly('cat a.txt | tee out.txt'), false);
});

// --- подстановка ------------------------------------------------------------
// Подстановка сама по себе не запрет: её содержимое — тоже команда, и судится
// оно так же. Иначе гвард спорил бы с рецептами соседних гвардов, где
// подстановка прямо предписана.
test('содержимое подстановки судится как отдельная команда', () => {
  assert.equal(readOnly('echo "$(cat mypid)"'), true);
  assert.equal(readOnly('kill "$(cat mypid)"'), false);
  assert.equal(readOnly('cat "$(rm -f x && echo y)"'), false);
  assert.equal(readOnly('echo `cat version.txt`'), true);
});

// --- обёртки и префиксы -----------------------------------------------------
test('обёртки снимаются, а не принимаются за команду', () => {
  assert.equal(readOnly('timeout 30 cat README.md'), true);
  assert.equal(readOnly('timeout 30 rm README.md'), false);
  assert.equal(readOnly('bash -c "cat README.md"'), true);
  assert.equal(readOnly('bash -c "rm README.md"'), false);
  assert.equal(readOnly('LC_ALL=C.UTF-8 grep -n x README.md'), true);
  assert.equal(readOnly('LC_ALL=C.UTF-8 rm README.md'), false);
});

// --- имя, заданное путём ----------------------------------------------------
// Сверка по имени файла позволяла подложить свой бинарник рядом и назвать его
// как безопасную утилиту.
test('имя команды, заданное путём, доказательством не считается', () => {
  assert.equal(readOnly('./sed -n 1p README.md'), false);
  assert.equal(readOnly('/usr/bin/cat README.md'), false);
  assert.equal(readOnly('bin/grep x README.md'), false);
});

// --- флаг-исполнитель у безобидной утилиты ----------------------------------
test('флаг, запускающий чужую команду, снимает доказательство', () => {
  assert.equal(readOnly('find . -name "*.js"'), true);
  assert.equal(readOnly('find . -name "*.js" -delete'), false);
  assert.equal(readOnly('find . -exec rm {} ;'), false);
  assert.equal(readOnly('sed -n 1p README.md'), true);
  assert.equal(readOnly('sed -i s/a/b/ README.md'), false);
});

// --- подкоманды одного инструмента ------------------------------------------
test('у одного инструмента читающая и пишущая подкоманды разведены', () => {
  assert.equal(readOnly('git show HEAD'), true);
  assert.equal(readOnly('git push origin main'), false);
  assert.equal(readOnly('npm ls'), true);
  assert.equal(readOnly('npm publish'), false);
});

// --- перенаправления --------------------------------------------------------
// Отвод в пустое устройство и дескрипторы записью в дерево не являются — иначе
// половина обычных читающих вызовов получала бы отказ.
test('отвод в пустое устройство записью не считается, а в файл — считается', () => {
  assert.equal(readOnly('grep -q x README.md 2>/dev/null'), true);
  assert.equal(readOnly('ls -la > /dev/null'), true);
  assert.equal(readOnly('echo x > README.md'), false);
  assert.equal(readOnly('echo x >> README.md'), false);
});

// --- непонятное -------------------------------------------------------------
// Разбор сломался — это не разрешение: неизвестность трактуется в пользу
// вопроса, а не в пользу пропуска.
test('сломанный разбор даёт недоказанность, а не проход', () => {
  const verdict = classifyCommand('cat "не закрытая кавычка');
  assert.equal(verdict.readOnly, false);
  assert.equal(verdict.cause, 'unparsed');
});

test('незнакомая утилита недоказуема, а не разрешена', () => {
  assert.equal(readOnly('craft-sync --backlinks abc'), false);
  assert.equal(cause('craft-sync --backlinks abc'), 'unproven');
});

// --- причина отказа ---------------------------------------------------------
// Два состояния лечатся по-разному: пишущую команду ждать бессмысленно, а
// недоказуемую можно переформулировать знакомой утилитой.
test('вердикт различает пишущую команду и недоказуемую', () => {
  assert.equal(cause('rm README.md'), 'mutates');
  assert.equal(cause('craft-sync --backlinks abc'), 'unproven');
});

test('вердикт называет команду, из-за которой закрылся', () => {
  assert.equal(classifyCommand('cat a.txt && rm b.txt').offender, 'rm');
  assert.equal(classifyCommand('craft-sync --write').offender, 'craft-sync');
});
