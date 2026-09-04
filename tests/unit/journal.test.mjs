// Журнал событий сессии: общая часть. Проверяется то, что читают ДВА потока
// работ после этой карточки, — форма строки и словарь операций, — и то, на чём
// стоит инстинкт-контур: отбор сигнальных строк по отметке.
//
// Имён инструментов в кейсах нет ровно там, где их нет в коде: общая часть
// получает ОБЛАСТЬ вызова и его ФОРМУ данными, и кейс подаёт их так же. Кейсы
// адаптера (какой инструмент чем является) живут отдельно — в наборе кейсов
// хуков, где событие приходит целиком.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  OPS, SIGNAL_OPS, fact, appendFact, factOf, readSignals, head,
} from '../../.claude/hooks/lib/journal.js';

// Адаптеры, как их собирает хук: разбор команды и вопрос про игнор репозитория.
const ADAPTERS = {
  commandWrites: (text) => ({
    mutates: /^git commit\b/.test(text),
    targets: text.includes('>') ? [text.split('>').pop().trim()] : [],
  }),
  ignored: () => false,
};

test('четыре поля стоят у КАЖДОЙ строки, какой бы ни была операция', () => {
  const line = fact({ op: OPS.READ, tool: 'какой-то', callId: 'c1', at: 0 });
  assert.deepEqual(Object.keys(line), ['ts', 'op', 'tool', 'call_id']);
  assert.equal(line.ts, '1970-01-01T00:00:00.000Z');
  // Идентификатора вызова у события может не быть — тогда пустая строка, а не
  // пропущенный ключ: читателю не приходится знать, у какого op какая форма.
  const noCall = fact({ op: OPS.INCIDENT, at: 0 });
  assert.deepEqual(Object.keys(noCall), ['ts', 'op', 'tool', 'call_id']);
  assert.equal(noCall.call_id, '');
  assert.equal(noCall.tool, '');
});

test('голова текста режется по БАЙТАМ и не рвёт символ', () => {
  // Кириллица — два байта на символ: посимвольный срез дал бы другую длину, а
  // срез по байтам без починки — битый хвост.
  assert.equal(head('абвг', 4), 'аб');
  assert.equal(head('строка\nвторая', 100), 'строка вторая', 'перевод строки схлопывается');
  assert.equal(head('', 100), '');
});

test('чтение: цель названа адаптером либо пуста, но не выдумана', () => {
  assert.deepEqual(
    factOf({ reads: true }, { kind: 'read', paths: ['/repo/a.js'] }, ADAPTERS),
    { op: OPS.READ, targets: ['/repo/a.js'] },
  );
  // Читающий вызов, у которого адаптер файла не назвал (поиск, сеть,
  // перечисление): факт чтения есть, цели нет. Пустой список — честный ответ;
  // догадка на его месте разрешила бы гварду 2.1 не то.
  assert.deepEqual(
    factOf({ reads: true }, {}, ADAPTERS),
    { op: OPS.READ, targets: [] },
  );
});

test('ход самой сессии строки не даёт', () => {
  assert.equal(factOf({ session: true }, { kind: 'edit', path: '/repo/a.js' }, ADAPTERS).op, '');
});

test('запись: цель берётся у адаптера, эфемерное записью не считается', () => {
  assert.deepEqual(
    factOf({}, { kind: 'edit', path: '/repo/a.js' }, ADAPTERS),
    { op: OPS.WRITE, targets: ['/repo/a.js'] },
  );
  // Правка в эфемерном пути — не запись мира, той же линейкой, какой это считают
  // план-гейт и метрики. Иначе два пути к одному вопросу разошлись бы.
  assert.equal(factOf({}, { kind: 'edit', path: '/tmp/x' }, ADAPTERS).op, '');
  assert.deepEqual(
    factOf({}, { kind: 'command', text: 'printf x > /repo/out.txt' }, ADAPTERS),
    { op: OPS.WRITE, targets: ['/repo/out.txt'] },
  );
  // Правка репозитория без перенаправления: запись есть, целей нет. Значит по
  // ДЛИНЕ списка про «менял ли» судить нельзя — на это отвечает op.
  assert.deepEqual(
    factOf({}, { kind: 'command', text: 'git commit -m x' }, ADAPTERS),
    { op: OPS.WRITE, targets: [] },
  );
  // Вызов, который мир трогает, но формы у него нет (сторонний сервер): запись
  // есть, цель не названа.
  assert.deepEqual(factOf({}, {}, ADAPTERS), { op: OPS.WRITE, targets: [] });
});

test('нет адаптера команды — строка есть, и в ней имя недостающего', () => {
  // Молчание тут читалось бы как «ничего не делал» — то есть как факт, которого
  // никто не устанавливал (решение 8).
  assert.deepEqual(
    factOf({}, { kind: 'command', text: 'printf x > /repo/out.txt' }, {}),
    { op: OPS.UNKNOWN, unsupported: 'write-targets' },
  );
});

// --- отбор сигналов ----------------------------------------------------------

function withJournal(lines, run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'journal-test.'));
  const file = path.join(dir, 'journal.jsonl');
  for (const rec of lines) appendFact(file, rec);
  try {
    return run(file, dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('сигнальными считаются ошибка и сигнал инцидента, и только они', () => {
  assert.deepEqual([...SIGNAL_OPS], [OPS.ERROR, OPS.INCIDENT]);
  withJournal([
    fact({ op: OPS.READ, tool: 'т', callId: 'c1' }, { targets: ['/repo/a.js'] }),
    fact({ op: OPS.ERROR, tool: 'т', callId: 'c2' }, { text: 'упало' }),
    fact({ op: OPS.WRITE, tool: 'т', callId: 'c3' }, { targets: ['/repo/b.js'] }),
    fact({ op: OPS.INCIDENT }, { text: 'ты сломал' }),
    fact({ op: OPS.UNKNOWN, tool: 'т', callId: 'c4' }, { unsupported: 'write-targets' }),
  ], (file) => {
    const { records } = readSignals(file);
    assert.deepEqual(records.map((r) => r.op), [OPS.ERROR, OPS.INCIDENT]);
  });
});

// Проба на дефект «охрана смотрит не в то поле»: отбор идёт по ПОЛЮ `op`, а не
// по тому, что слово встретилось где-то в строке. Без неё зелёный отбор ничего
// не значил бы — он совпал бы со строчным поиском на всех прежних кейсах.
test('отбор идёт по полю op, а не по слову в строке', () => {
  withJournal([
    fact({ op: OPS.READ, tool: 'т', callId: 'c1' }, { targets: ['/repo/error-handling.js'] }),
    fact({ op: OPS.WRITE, tool: 'т', callId: 'c2' }, { targets: ['/repo/incident.md'] }),
    // Строка, внутри которой лежит ТЕКСТ, похожий на сигнальную строку целиком.
    fact({ op: OPS.READ, tool: 'т', callId: 'c3' }, { targets: ['/repo/a.js'], note: '{"op":"error"}' }),
  ], (file) => {
    assert.deepEqual(readSignals(file).records, [], 'ни одна из этих строк сигналом не является');
  });
});

test('отметка отсекает уже разобранное и не отсекает нового', () => {
  withJournal([
    fact({ op: OPS.ERROR, tool: 'т', callId: 'c1' }, { text: 'первая' }),
    fact({ op: OPS.ERROR, tool: 'т', callId: 'c2' }, { text: 'вторая' }),
  ], (file) => {
    const all = readSignals(file);
    assert.equal(all.records.length, 2);
    // Размер, который хук печатает в директиве, и есть отметка «разобрано досюда».
    assert.deepEqual(readSignals(file, all.size).records, []);
    const firstLine = fs.readFileSync(file, 'utf8').split('\n')[0];
    const after = Buffer.byteLength(`${firstLine}\n`, 'utf8');
    assert.deepEqual(readSignals(file, after).records.map((r) => r.text), ['вторая']);
  });
});

test('отметка БОЛЬШЕ журнала читается как «журнал начался заново»', () => {
  // Иначе сигналы новой сессии молча считались бы разобранными — контур замолчал
  // бы ровно там, где обязан говорить.
  withJournal([
    fact({ op: OPS.ERROR, tool: 'т', callId: 'c1' }, { text: 'упало' }),
  ], (file) => {
    assert.equal(readSignals(file, 10 ** 6).records.length, 1);
  });
});

test('журнала нет — пустой ответ, а не падение', () => {
  assert.deepEqual(readSignals('/нет-такого-каталога/journal.jsonl'), { records: [], size: 0 });
  assert.deepEqual(readSignals(''), { records: [], size: 0 });
});

test('битая строка журнала не роняет разбор соседних', () => {
  withJournal([], (file) => {
    fs.appendFileSync(file, '{битое\n');
    appendFact(file, fact({ op: OPS.ERROR, tool: 'т', callId: 'c1' }, { text: 'упало' }));
    assert.deepEqual(readSignals(file).records.map((r) => r.text), ['упало']);
  });
});
