#!/usr/bin/env node
// PostToolUse на ExitPlanMode: план одобрен — периметр его строк «- где:»
// ДОПИСЫВАЕТСЯ в маркер-файл гейта. Маркер больше не булев «одобрено»: это
// список целей, и гейт (universal-guard-plan-gate) открывает только их.
// Одобрения складываются: второй план сессии — дельта (guard-plan-delta), его
// цели добавляются к прежним, ничего не стирая. Гасит список только смена
// сессии — файл живёт в /tmp с id.
//
// Цели извлекаются из строк «- где:» СУЩНОСТНЫХ заголовков (## [тип · …]):
//   - файловые — токены в бэктиках с косой чертой или точкой в имени;
//   - Craft — последняя ссылка docs.craft.do строки: сегмент после /x/, а без
//     него — последний сегмент пути (корневой блок; открывает только его).
// Захват лишних токенов (пояснения в бэктиках) принят: периметр — грубый
// фильтр, точность даёт классификатор содержания (tools/plan-scope-classifier).
//
// Защита от протечки привязана к ИСТОЧНИКУ пути: путь, выведенный из пустого
// session-id (общий default), для записи не используется — им делили бы
// периметр параллельные headless-прогоны; путь из env-переопределения
// используется всегда (тесты герметичны через него).
//
// Файла плана нет или целей не извлеклось — список не пишется, гейт закрыт:
// иначе агент открывал бы гейт целиком, просто не оставив метку плана.
import fs from 'node:fs';
import {
  planGateMarker, approvedPlans, planFileMarker, planCriticRuns, planCriticRound,
} from './lib/paths.js';
import { appendRecord } from './lib/qa-window.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

const marker = planGateMarker();

// Сущностный заголовок опознаётся на ЛЮБОМ уровне, как у разборщика юнитов в
// universal-guard-plan-delta: у плана с юнитами глубина сущности уезжает на
// уровень ниже, и жёсткие две решётки давали нулевой периметр — то есть гейт,
// закрытый сразу после одобрения.
function addressLines(text) {
  const found = [];
  let entity = false;
  for (const line of text.split('\n')) {
    if (/^#+[ \t]*\[/.test(line)) { entity = true; continue; }
    if (/^#/.test(line)) { entity = false; continue; }
    if (entity && /^[ \t]*-[ \t]*где:/.test(line)) found.push(line);
  }
  return found;
}

function targetsOfPlan(text) {
  const targets = [];
  for (const line of addressLines(text)) {
    for (const m of line.matchAll(/`[^`]+`/g)) {
      const token = m[0].replace(/`/g, '');
      if (/[/.]/.test(token)) targets.push(token);
    }
    // Путь, записанный markdown-ссылкой: целью служит АДРЕС, не подпись. Подпись
    // по правилу адресации обрезана до узнаваемого корня и ни с одним путём на
    // диске не совпадёт — периметр по ней открылся бы в никуда. Веб-адреса сюда
    // не идут: Craft-ссылку разбирает своя ветка ниже.
    for (const m of line.matchAll(/\]\([^)]+\)/g)) {
      const href = m[0].slice(2, -1);
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(href)) continue;
      if (/[/.]/.test(href)) targets.push(href);
    }
    const urls = line.match(/https:\/\/docs\.craft\.do\/[^) ]+/g);
    if (urls) {
      const url = urls[urls.length - 1];
      const at = url.lastIndexOf('/x/');
      if (at >= 0) targets.push(url.slice(at + 3).split('/')[0]);
      else targets.push(url.slice(url.lastIndexOf('/') + 1));
    }
  }
  return targets;
}

function planPath() {
  if (process.env.CRAFT_PLAN_FILE) return process.env.CRAFT_PLAN_FILE;
  try {
    return fs.readFileSync(planFileMarker(), 'utf8').replace(/\n+$/, '');
  } catch {
    return '';
  }
}

if (marker) {
  const plan = planPath();
  let text = null;
  try {
    if (plan) text = fs.readFileSync(plan, 'utf8');
  } catch { /* файла плана нет — периметр не пополняется */ }

  const targets = text === null ? [] : targetsOfPlan(text);
  if (targets.some((t) => /\S/.test(t))) {
    let current = '';
    try {
      current = fs.readFileSync(marker, 'utf8');
    } catch { /* маркера ещё нет */ }
    // Пустые строки выбрасываются, порядок первого появления сохраняется.
    const seen = new Set();
    const lines = [];
    for (const line of [...current.split('\n'), ...targets]) {
      if (!/\S/.test(line) || seen.has(line)) continue;
      seen.add(line);
      lines.push(line);
    }
    try {
      fs.writeFileSync(marker, lines.length ? `${lines.join('\n')}\n` : '');
    } catch { /* не записалось — гейт останется закрытым */ }

    // Накопитель одобренных ТЕКСТОВ — вход сверки содержания у гейта: правка
    // старой цели сверяется со своим планом, а правка файла плана после
    // одобрения одобренного не меняет. Пишется тем же вызовом, что цели
    // (нет целей — нет и записи), окно — последние 5 одобрений.
    appendRecord(approvedPlans(), `=== ОДОБРЕНИЕ ===\n${text}\n`, 5, /^=== ОДОБРЕНИЕ ===$/);
  }
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
