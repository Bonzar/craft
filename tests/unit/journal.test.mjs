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
  commandReads: (text) => {
    if (text.startsWith('смотрю ')) return { reads: true, proven: true, targets: [text.slice('смотрю '.length)] };
    // «Разбор ЗНАЕТ, что она меняет» против «ничего не понял» — разные ответы.
    return { reads: false, mutates: text.startsWith('понятная'), targets: [] };
  },
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
  // Кириллица — два байта на символ: посимвольный срез дал бы другую длину.
  assert.equal(head('абвг', 4), 'аб');
  // А это ГРАНИЦА ПОСРЕДИ символа, и прежняя фикстура её не задевала: 4 байта
  // приходились ровно на стык. Без отступа к началу символа хвост превращается в
  // знак замены — на 300-байтовой границе русского текста это половина случаев.
  assert.equal(head('абвг', 3), 'а');
  assert.equal(head('日本語', 4), '日');
  assert.ok(!head('абвгд', 5).includes('\uFFFD'), 'знака замены в голове не бывает');
  assert.equal(head('строка\nвторая', 100), 'строка вторая', 'перевод строки схлопывается');
  assert.equal(head('', 100), '');
  // Неположительный лимит: отрицательный конец у среза буфера отсчитывается ОТ
  // КОНЦА, и «обрежь до нуля» отдавало почти весь текст.
  assert.equal(head('целая строка', -1), '');
  assert.equal(head('целая строка', 0), '');
});

test('чтение: цель названа адаптером либо пуста, но не выдумана', () => {
  assert.deepEqual(
    factOf({ reads: true }, { kind: 'read', paths: ['/repo/a.js'] }, ADAPTERS),
    { op: OPS.READ, targets: ['/repo/a.js'] },
  );
  // Читать нечего ПО ПРИРОДЕ вызова (список работы, перечисление, поиск в сети):
  // пустой список — факт, и имени недостающего рядом с ним быть не должно.
  assert.deepEqual(
    factOf({ reads: true }, { kind: 'read-nothing' }, ADAPTERS),
    { op: OPS.READ, targets: [] },
  );
});

// Два случая, которые пустой список СХЛОПЫВАЕТ, если их не развести: «читать было
// нечего» и «читал, а назвать нечем». Гвард чтения отвечает на них по-разному, и
// читатель обязан их различать — поэтому второй несёт имя.
test('«назвать прочитанное нечем» — это имя, а не пустой список', () => {
  const named = factOf({ reads: true }, {}, ADAPTERS);
  assert.deepEqual(named, { op: OPS.READ, targets: [], unsupported: 'read-targets' });
  assert.notDeepEqual(named, factOf({ reads: true }, { kind: 'read-nothing' }, ADAPTERS));
});

// Запись в базу заметок адресуется не путём файла. Адреса нет — запись всё равно
// ЕСТЬ: пропав, она унесла бы с собой ровно то изменение, ради сборки которого
// журнал и заведён.
test('запись в базу заметок: адрес либо назван, либо назван недостающим', () => {
  assert.deepEqual(
    factOf({}, { kind: 'note', ref: 'ABC123' }, ADAPTERS),
    { op: OPS.WRITE, targets: ['ABC123'] },
  );
  assert.deepEqual(
    factOf({}, { kind: 'note', ref: '' }, ADAPTERS),
    { op: OPS.WRITE, targets: [], unsupported: 'note-write-target' },
  );
});

// Доказанно читающая команда — тоже чтение, и цели у него от адаптера
// интерпретатора. Без этого гвард «не правь того, чего не читал» давал бы ложный
// отказ на файле, который агент посмотрел командой.
test('команда: доказанное чтение даёт цели, недоказанная не даёт строки', () => {
  assert.deepEqual(
    factOf({}, { kind: 'command', text: 'смотрю /repo/a.js' }, ADAPTERS),
    { op: OPS.READ, targets: ['/repo/a.js'] },
  );
  // Разбор ЗНАЕТ, что команда меняет состояние, а целей не нашлось: запись
  // установлена, не названа только цель — молчать про неё нельзя.
  assert.deepEqual(
    factOf({}, { kind: 'command', text: 'понятная команда' }, ADAPTERS),
    { op: OPS.WRITE, targets: [], unsupported: 'write-targets' },
  );
  // А вот «ничего не понял» молчанием быть не может: такая команда могла
  // переписать пол-репозитория, и пустота выдала бы неизвестность за спокойствие.
  assert.deepEqual(
    factOf({}, { kind: 'command', text: 'непонятная' }, ADAPTERS),
    { op: OPS.UNKNOWN, unsupported: 'command-effect' },
  );
  // Адаптера чтения нет вовсе — вопрос остался без ответа, и это тоже имя.
  assert.deepEqual(
    factOf({}, { kind: 'command', text: 'непонятная' }, { commandWrites: () => ({}) }),
    { op: OPS.UNKNOWN, unsupported: 'read-targets' },
  );
});

// Область вызова отвечает РАНЬШЕ формы: строки нет ни при какой форме. Одной
// правки тут мало — на ней тот же пустой `op` даёт и общий признак «вызов мир не
// трогает», и кейс зеленел бы при снятой проверке области, то есть не проверял бы
// её вовсе. Красным его делают формы, где два механизма расходятся: адрес базы
// заметок и команда интерпретатора — обе дали бы строку, спроси область позже.
test('ход самой сессии строки не даёт — ни при какой форме вызова', () => {
  assert.equal(factOf({ session: true }, { kind: 'edit', path: '/repo/a.js' }, ADAPTERS).op, '');
  assert.equal(factOf({ session: true }, { kind: 'note', ref: 'ABC123' }, ADAPTERS).op, '');
  assert.equal(factOf({ session: true }, { kind: 'command', text: 'смотрю /repo/a.js' }, ADAPTERS).op, '');
  assert.equal(factOf({ session: true }, {}, ADAPTERS).op, '');
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
  // Вызов, который мир трогает, а формы у него нет (сторонний сервер, подагент):
  // «менял» тут — консервативное умолчание гейта, а не установленный факт, и
  // журнал обязан сказать это вслух.
  assert.deepEqual(
    factOf({}, {}, ADAPTERS),
    { op: OPS.WRITE, targets: [], unsupported: 'call-shape' },
  );
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
    const { status, records } = readSignals(file);
    assert.equal(status, 'ok');
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

// Две разные вещи, которые молчание схлопнуло бы в одну: журнала ЕЩЁ НЕТ (сессия
// ничего не делала — это факт) и журнал ЕСТЬ, но не читается (ответа нет вовсе).
// Схлопнув их, отказ чтения выдали бы за спокойную сессию, то есть поменяли бы
// знак.
test('«журнала нет» — факт, «журнал не читается» — имя', () => {
  assert.deepEqual(
    readSignals('/нет-такого-каталога/journal.jsonl'),
    {
      status: 'ok', records: [], size: 0, from: 0, end: 0,
    },
  );
  assert.deepEqual(readSignals(''), {
    status: 'ok', records: [], size: 0, from: 0, end: 0,
  });
  // Каталог на месте файла: открыть его нельзя, и это не ENOENT.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'journal-unreadable-test.'));
  try {
    const answer = readSignals(dir);
    assert.equal(answer.status, 'unsupported');
    assert.equal(answer.capability, 'journal-read');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('битая строка журнала не роняет разбор соседних', () => {
  withJournal([], (file) => {
    fs.appendFileSync(file, '{битое\n');
    appendFact(file, fact({ op: OPS.ERROR, tool: 'т', callId: 'c1' }, { text: 'упало' }));
    assert.deepEqual(readSignals(file).records.map((r) => r.text), ['упало']);
  });
});

// Отметка из ПРОШЛОЙ жизни журнала (его снесли, подменили, он усох). Разбор
// читает с начала — и обязан СКАЗАТЬ об этом, иначе держатель отметки, который
// двигает её только вперёд, заклинит навсегда на одних и тех же строках.
test('отметка больше журнала: читаем с начала И говорим об этом', () => {
  withJournal([
    fact({ op: OPS.ERROR, tool: 'т', callId: 'c1' }, { text: 'упало' }),
  ], (file) => {
    const answer = readSignals(file, 10 ** 6);
    assert.equal(answer.records.length, 1);
    assert.equal(answer.from, 0, 'фактическое начало чтения возвращается вызывающему');
  });
});

// Конец разобранного — граница ПОСЛЕДНЕЙ ПОЛНОЙ строки, и считается она в
// БАЙТАХ. Производитель дописывает журнал на каждом вызове, и конец хода приходит
// сразу за одним из них: отметив недописанную строку разобранной, её сигнал
// потеряли бы навсегда — разбор молча пропускает обрывок как битую строку.
test('конец разобранного не встаёт посреди строки и считается в байтах', () => {
  withJournal([
    fact({ op: OPS.ERROR, tool: 'т', callId: 'c1' }, { text: 'первая' }),
  ], (file) => {
    const whole = readSignals(file);
    assert.equal(whole.end, whole.size, 'дописанный журнал разобран целиком');
    // Хвост без перевода строки — это недописанная строка, и в разобранное она
    // не входит.
    fs.appendFileSync(file, '{"op":"error","text":"недопис');
    const torn = readSignals(file);
    assert.equal(torn.end, whole.size, 'граница осталась на конце последней ПОЛНОЙ строки');
    assert.ok(torn.size > torn.end, 'а размер файла уже больше');
    // Кириллица: символьная длина здесь меньше байтовой, и смещение обязано быть
    // байтовым — иначе горизонт уезжает назад, в середину разобранной строки.
    assert.equal(whole.end, Buffer.byteLength(fs.readFileSync(file, 'utf8').split('\n')[0] + '\n', 'utf8'));
  });
});
