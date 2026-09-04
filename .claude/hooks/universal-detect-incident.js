#!/usr/bin/env node
// UserPromptSubmit hook: сообщение Влада несёт сигнал инцидента — в контекст
// уходит жёсткая директива и живое тело «Разбора инцидента» (его кладёт в кэш
// craft-inject-incident на старте сессии). Док оказывается перед агентом в момент
// сигнала, а не тогда, когда агент сам решит его открыть.
//
// Маркеры живут в incident-markers.txt (по регулярке на строку, строки с решёткой
// игнорируются) — набор растёт, не трогая эту логику. Сама процедура разбора
// требует дописать пропущенный маркер туда всякий раз, когда реальный инцидент
// проехал мимо детектора.
//
// Fail quiet на всём неожиданном: сломанный детектор не должен мешать сообщению
// Влада пройти.
//
// Регистр кириллицы: bash-версии приходилось выставлять UTF-8 локаль, иначе grep
// -i не складывал регистры и «Инцидент» с большой буквы проезжал мимо. Здесь
// регистронезависимость даёт сама регулярка, и локаль ни при чём.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readEvent } from './lib/event-claude.js';
import { hookOnce } from './lib/once.js';
import { appendFlag } from './lib/decision-log.js';
import { observeBuffer, incidentClosureMarker, sessionId } from './lib/paths.js';

// Резолв через симлинки: установленный симлинком в ~/.claude хук обязан найти
// incident-markers.txt рядом с НАСТОЯЩИМ файлом в репозитории.
const selfPath = fileURLToPath(import.meta.url);
let dir = path.dirname(selfPath);
try {
  dir = path.dirname(fs.realpathSync(selfPath));
} catch { /* нечего резолвить — берём каталог как есть */ }
const markersFile = path.join(dir, 'incident-markers.txt');
const anchorsFile = process.env.CRAFT_SERVICE_ANCHORS || path.join(dir, 'service-anchors.txt');
const projectDir = process.env.CLAUDE_PROJECT_DIR || path.resolve(dir, '..', '..');
const cacheFile = path.join(projectDir, '.claude', 'craft-incident-context.md');

const { raw, core, prompt } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);
if (!prompt) process.exit(0);

// Строки файла-словаря: пустые и начатые решёткой не в счёт.
function entries(file) {
  try {
    return fs.readFileSync(file, 'utf8').split('\n')
      .filter((line) => line !== '' && !line.startsWith('#'));
  } catch {
    return [];
  }
}

// Служебное сообщение репликой Влада не является: маркер в вердикте подагента или в
// тексте стоп-хука — не сигнал инцидента. Якоря — литералы, сверяются с началом
// ВСЕГО сообщения, а не любой его строки.
for (const anchor of entries(anchorsFile)) {
  if (prompt.startsWith(anchor)) process.exit(0);
}

if (!fs.existsSync(markersFile)) process.exit(0);

// Сверка построчная, как у grep: точка в регулярке перевод строки не переходит.
const lines = prompt.split('\n');
const matched = entries(markersFile).some((pattern) => {
  let re;
  try {
    re = new RegExp(pattern, 'i');
  } catch {
    return false; // нечитаемый паттерн словаря детектор не роняет
  }
  return lines.some((line) => re.test(line));
});
if (!matched) process.exit(0);

function headBytes(text, limit) {
  const cut = Buffer.from(`${text}\n`, 'utf8').subarray(0, limit);
  let end = cut.length;
  while (end > 0 && cut[end - 1] === 0x0a) end -= 1;
  return cut.subarray(0, end).toString('utf8');
}

// Сигнал — в буфер инстинкт-контура (кормит дистилляцию кандидатов в конце хода).
try {
  fs.appendFileSync(observeBuffer(), `incident-signal: ${headBytes(prompt, 200).replace(/\n/g, ' ')} \n`);
} catch { /* буфер не пополнился — расходник */ }

// Взвод гейта закрытия разбора (universal-stop-incident-closure): маркер
// «в сессии есть неразобранный сигнал». Отметка «уже напоминали» снимается —
// КАЖДЫЙ новый сигнал гейтится заново (два инцидента подряд в одной сессии).
if (sessionId()) {
  const armed = incidentClosureMarker();
  try {
    fs.writeFileSync(armed, '');
  } catch { /* не взвелось — гейт закрытия просто не сработает */ }
  try {
    fs.rmSync(`${armed.replace(/\.armed$/, '')}.reminded`, { force: true });
  } catch { /* отметки и не было */ }
}

let out = '⚠️ СИГНАЛ ИНЦИДЕНТА в сообщении Влада. Первым действием веди разбор строго по «⚙️ SKILL: Разбор инцидента» (тело ниже). Причину, урок или правку не формулируй, пока не свернул к нему. Вариант, нарушающий уже записанное правило, не предлагается; нарушено записанное правило → нужен ДРУГОЙ рычаг, не копия правила рядом; без рычага инцидент не закрыт.\n\n';
let cache = null;
try {
  if (fs.statSync(cacheFile).isFile()) cache = fs.readFileSync(cacheFile, 'utf8');
} catch { /* кэша нет — уйдёт запасной текст */ }
if (cache !== null) {
  out += `----- живое тело «Разбор инцидента» (кэш SessionStart) -----\n${cache}`;
} else {
  out += '(кэш тела отсутствует. Доступен Craft MCP → прочитай скилл живьём: blocks get cbb1ba47-c05b-60b5-f86e-16c05b77bb4f --depth -1. Craft недоступен → веди разбор по каркасу: 1) причина — какое действие/допущение привело к дефекту; 2) как было надо; 3) исправить результат; 4) записать переносимый урок «при [сигнале] → [действие]» с РЫЧАГОМ соблюдения (гейт, хук, чек-лист, перестановка правила в точку решения) — для кода см. скилл code-incident. Без рычага инцидент не закрыт.)\n';
}
// Сигнал — в журнал решений: хук метрик считает срабатывания детектора по нему, а
// не по stdout. Тот же канал, что и у решения, и живёт он дольше процесса.
appendFlag(readEvent(), 'incident');
process.stdout.write(out);
