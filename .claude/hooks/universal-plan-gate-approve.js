#!/usr/bin/env node
// PostToolUse на ExitPlanMode: одобренный план принимается в реестр одобренного.
//
// Приём — ТОТ ЖЕ путь, что у реплики и кнопочного ответа: общий помощник, общий
// разбор, общая семантическая дедупликация. Структуру решает разбор по смыслу:
// сколько тут целей, сколько задач и как они вложены. Прежний разбор по форме —
// сущностные заголовки и строки «где:» — снят: форма плана не закреплена и
// каждый раз своя, а по ней периметр то пустел, то захватывал лишнее.
//
// План принимается СИНХРОННО, в отличие от реплики: правки идут сразу за
// одобрением, и ждать тут нечего.
//
// Файла плана нет или разбор не удался — реестр не пополняется, и гейт остаётся
// закрытым: иначе агент открывал бы работу, просто не оставив следа одобрения.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import {
  planFileMarker, planCriticRuns, planCriticRound, approvalRegistry,
} from './lib/paths.js';
import { ingestDeadlineMs } from './lib/classifier.js';
import { childEnv } from './lib/metrics.js';
import { hookOnce } from './lib/once.js';
import { readEvent } from './lib/event-claude.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

// Уступка второму вызову события: хук зарегистрирован в двух контурах, а внутри
// теперь вызов модели — дубль стоил бы второго разбора плана.
const { raw, core } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);

function planPath() {
  if (process.env.CRAFT_PLAN_FILE) return process.env.CRAFT_PLAN_FILE;
  try {
    return fs.readFileSync(planFileMarker(), 'utf8').replace(/\n+$/, '');
  } catch {
    return '';
  }
}

// Приём плана в реестр — ТОТ ЖЕ путь, что у реплики и кнопочного ответа:
// общий помощник, общий разбор, общая семантическая дедупликация. Разбор по
// заголовкам, который жил здесь раньше, снят: структуру решает смысл, а форма
// плана не закреплена и каждый раз своя.
//
// План принимается СИНХРОННО, в отличие от реплики: правки идут сразу за
// одобрением, и ждать тут нечего.
// След приёма со стороны хука. Сам приём пишет в тот же файл ход разбора, но
// его записей не будет вовсе, когда приём не запустился, — а это исход, который
// снаружи выглядит ровно как удавшийся: гейт закрыт, реестр пуст, объяснения
// нет. Поэтому запуск и его исход хук отмечает сам.
function trace(line) {
  const file = approvalRegistry();
  if (!file) return;
  try {
    fs.appendFileSync(`${file}.ingest.log`, `${new Date().toISOString()} plan ${line}\n`);
  } catch { /* след не записался — работу приёма это не меняет */ }
}

function ingestPlan(planFile) {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const ingest = path.resolve(here, '..', '..', 'tools', 'registry-ingest.mjs');
  if (!fs.existsSync(ingest)) {
    trace(`приём не запущен: нет ${ingest}`);
    return;
  }
  const started = Date.now();
  try {
    const res = spawnSync(process.execPath, [ingest, 'plan', planFile, approvalRegistry()], {
      stdio: 'ignore',
      timeout: ingestDeadlineMs(),
      env: childEnv(),
    });
    const spent = Math.round((Date.now() - started) / 1000);
    // Убитый по сроку процесс приходит с signal, а не с кодом: это тот самый
    // исход, ради которого срок и выводится из бюджета приёма.
    const how = res.signal ? `убит по сроку (${res.signal})` : `код ${res.status}`;
    trace(`приём кончился за ${spent} с: ${how}`);
  } catch (err) {
    trace(`приём не удался: ${err && err.message}`);
    /* реестр не пополнится, гейт останется закрытым */
  }
}

const plan = planPath();
if (plan) {
  try {
    fs.accessSync(plan, fs.constants.R_OK);
    ingestPlan(plan);
  } catch {
    trace(`приём не запущен: файл плана недоступен (${plan})`);
  }
} else {
  trace('приём не запущен: путь к файлу плана неизвестен');
}

// Показ состоялся — обкатка кончилась: счётчик прогонов критика начинает следующую с нуля.
// Обкатка привязана к плану, а не к ходу, поэтому обнуляет её именно одобрение, а не
// реплика Влада — по реплике плато было недостижимо в живом диалоге.
// Память версии круга живёт внутри одной обкатки: показ её закрывает вместе со счётчиком.
for (const file of [planCriticRuns(), planCriticRound()]) {
  try {
    fs.rmSync(file, { force: true });
  } catch { /* нечего снимать */ }
}
