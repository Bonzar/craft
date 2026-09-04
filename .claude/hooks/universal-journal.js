#!/usr/bin/env node
// PostToolUse hook (все инструменты): ЖУРНАЛ СОБЫТИЙ СЕССИИ. Строка на каждое
// чтение, строка на каждую запись и строка на каждую ошибку инструмента.
//
// Зачем. Читатели слоя — гвард «не правь то, чего не читал» и сборка набора
// изменений в базе заметок — обязаны брать факты из ОДНОГО места, одинакового у
// любого харнеса. Транскрипт таким местом быть не может: он есть не у всех и у
// каждого свой. Поэтому строка собирается из канонического события — ядро
// (инструмент, вход, идентификатор вызова) плюс то, что даёт обёртка.
//
// Разбора «что за операция и над чем» здесь нет: область вызова и его форму
// приносит адаптер харнеса (journal-claude.js), цели команды — адаптер
// интерпретатора, а решает по ним общая часть (lib/journal.js). Хук — только
// проводка: собрать адаптеры, спросить факт, положить строку.
//
// Сюда же слит прежний СИГНАЛЬНЫЙ БУФЕР (universal-observe-buffer): ошибка
// инструмента перестала быть отдельным эфемерным файлом и стала обычной строкой
// журнала с `op: error`. Инстинкт-контур читает её оттуда же.
//
// Ошибка вытесняет чтение и запись: у провалившегося вызова неизвестно, что он
// успел сделать, и записать ему `write` значило бы назвать фактом догадку.
//
// Стоит СРАЗУ ЗА наблюдателем и по той же причине: первое решение цепочки её
// обрывает, и производитель, стоящий последним, не звался бы ровно там, где
// что-то пошло не так.
//
// Уступки второму вызову здесь нет намеренно — её не было и у буфера: лишняя
// строка безобиднее пропущенной. Fail quiet: сломанный журнал ход не трогает.
import { readEvent, responseIsError } from './lib/event-claude.js';
import { EVENTS, missingFact, unsupported } from './lib/event.js';
import {
  OPS, fact, appendFact, factOf, head,
} from './lib/journal.js';
import { journalShape } from './lib/journal-claude.js';
import { toolScope } from './lib/tool-flags-claude.js';
import { commandTargets } from './lib/write-targets-bash.js';
import { gitMutates } from './lib/write-targets-git.js';
import { isIgnored } from './lib/repo-git.js';

// Адаптеры инструментов для общей части — те же, что у наблюдателя: команда
// интерпретатора даёт цели записи, git — правку, которая целями не видна, третьим
// идёт вопрос «игнорирует ли путь репозиторий».
const ADAPTERS = {
  commandWrites: (text) => ({ mutates: gitMutates(text), targets: commandTargets(text) }),
  ignored: isIgnored,
};

const ev = readEvent();
const { tool, input, response } = ev;

// Журнал пишет ТОЛЬКО состоявшийся вызов. Событие до вызова несёт то же имя
// инструмента и тот же вход, но вызова ещё не было — и его могут запретить;
// строка про него была бы не фактом, а намерением, выданным за факт. Условие
// стоит здесь, а не держится на одной регистрации: маршрут меняют, и тогда
// молчаливо журналировался бы отказ.
if (ev.event !== EVENTS.POST_TOOL) process.exit(0);

// Факт `journal` — путь, КУДА писать. Его даёт обёртка; нет его (сессии нет
// вовсе) — ответ `unsupported` С ИМЕНЕМ в служебный поток, а не молчаливый
// проход: молчание тут неотличимо от «сессия ничего не делала».
const REQUIRES = ['journal'];
const missing = missingFact(ev, REQUIRES);
if (missing) {
  process.stderr.write(`[journal] unsupported: ${unsupported(missing).capability}\n`);
  process.exit(0);
}
const log = ev.journal;

if (!tool) process.exit(0);

const at = Date.now();
const base = { tool, callId: ev.call_id, at };

// Ошибка инструмента опознаётся предикатом ОБЁРТКИ: форма ответа — форма
// харнеса, и журнал с метриками обязаны считать ошибкой одно и то же.
// Не-объект в ответе ошибкой не является: строка или число — не тот ответ, в
// котором ищут ошибку.
const shaped = response !== null && typeof response === 'object' && !Array.isArray(response);
if (shaped && responseIsError(response)) {
  // Откуда брать текст, решает форма ответа: у помеченного признаком ошибки он
  // лежит в содержимом, у прочих — в самом поле ошибки. Голова 300 байт — как у
  // прежнего буфера: строки читает дистиллятор, и менять их длину на переезде
  // незачем.
  const marked = response.is_error === true || response.isError === true;
  const body = marked
    ? (response.content ?? response.error ?? '')
    : (response.error ?? '');
  const text = head(typeof body === 'string' ? body : JSON.stringify(body), 300);
  if (text) appendFact(log, fact({ ...base, op: OPS.ERROR }, { text }));
  process.exit(0);
}

// Чтение или запись. Пустая операция значит «строки нет»: ход самой сессии
// (план, вопрос, список работы) ни тем, ни другим не является, и запись про него
// была бы шумом, а не фактом.
const what = factOf(toolScope(tool, input), journalShape(tool, input), ADAPTERS);
if (!what.op) process.exit(0);

appendFact(log, fact({ ...base, op: what.op }, what.op === OPS.UNKNOWN
  ? { unsupported: what.unsupported }
  : { targets: what.targets }));
