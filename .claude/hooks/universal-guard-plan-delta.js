#!/usr/bin/env node
// Показ плана, две роли по имени события:
//   до вызова  — не показывать Владу юнит, который он в этой сессии уже одобрял;
//   после      — одобренный план оставляет хеши своих юнитов в накопителе.
// Обе роли в одном файле, чтобы разборщик юнитов существовал в единственном
// виде: разъехавшиеся копии дали бы хеши, которые никогда не совпадут.
//
// Сравниваются только сущностные юниты — заголовок любого уровня, начинающийся
// с типа в квадратных скобках. Прочие разделы повторяются из плана в план по
// своей природе.
//
// Нормализация до хеша: пустые строки не учитываются, иначе разделитель перед
// следующим заголовком менял бы хеш одного и того же юнита. Строка цитаты
// заголовком не считается — дословный текст чужого плана живёт именно там, — но
// в тело юнита входит: иначе правка дословного текста не меняла бы хеш и новая
// редакция правила читалась бы как повтор старой.
//
// Вырезателя код-блоков нет намеренно, как и у критика: разбор заборов
// обманывается вложенностью и возвращает ноль юнитов, то есть глушит гейт молча.
// Ценой этого заголовок из примера кода посчитается юнитом — лишний хеш дешевле
// молчания.
//
// Аварийный выключатель — PLAN_DELTA=off. Fail open на всём неожиданном.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { sha256 } from './lib/hash.js';
import {
  planFileMarker, planDeltaStore, planDeltaSnapshot, buttonPlans, permissionWindow,
} from './lib/paths.js';
import { classify, classifierPath, classifierAvailable } from './lib/classifier.js';

if (process.env.PLAN_DELTA === 'off') process.exit(0);

const { raw, event, tool, name } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
if (tool !== 'ExitPlanMode') process.exit(0);
const eventName = name || 'PreToolUse';

function planPath() {
  if (process.env.CRAFT_PLAN_FILE) return process.env.CRAFT_PLAN_FILE;
  try {
    return fs.readFileSync(planFileMarker(), 'utf8').trim();
  } catch {
    return '';
  }
}

const plan = planPath();
let text = '';
try {
  text = fs.readFileSync(plan, 'utf8');
} catch {
  process.exit(0);
}

// Разбор файла на сущностные юниты: «хеш + заголовок» на каждый.
function units(source) {
  const found = [];
  let title = '';
  let body = '';
  const emit = () => {
    if (title) found.push({ hash: sha256(body), title });
  };
  for (const line of source.split('\n')) {
    if (line.trim() === '') continue;
    if (/^\s*>/.test(line)) {
      if (title) body += `${line}\n`;
      continue;
    }
    if (/^#+\s*\[/.test(line)) {
      emit();
      title = `[${line.slice(line.indexOf('[') + 1)}`;
      body = `${line}\n`;
      continue;
    }
    if (/^#+\s/.test(line)) {
      emit();
      title = '';
      body = '';
      continue;
    }
    if (title) body += `${line}\n`;
  }
  emit();
  return found;
}

const now = units(text);
if (now.length === 0) process.exit(0);

const store = planDeltaStore();

if (eventName === 'PostToolUse') {
  // Снимок последнего одобренного плана, а не копилка за всю сессию: юнит из
  // давней работы иначе блокировал бы новый план навсегда. Цена — повтор через
  // план обратно не ловится; ложный отказ дороже пропущенного повтора.
  try {
    fs.writeFileSync(store, `${now.map((u) => u.hash).join('\n')}\n`);
    // Рядом с хешами — ТЕКСТ одобренного плана: вход сравнения по смыслу.
    fs.copyFileSync(plan, planDeltaSnapshot());
  } catch { /* не записалось — следующая дельта просто не поймает повтор */ }
  process.exit(0);
}

// Вход сравнения по смыслу: микро-планы разрешений и окно нерастраченных
// указаний. Окно участвует наравне со сработавшими разрешениями: план, целиком
// покрытый свежим указанием Влада, не показывается — уже разрешено, правильный
// ход выполнять, а не собирать план.
function approvalsText() {
  let out = '';
  for (const file of [buttonPlans(), permissionWindow()]) {
    if (!file) continue;
    try {
      const part = fs.readFileSync(file, 'utf8');
      if (part) out += part;
    } catch { /* файла нет — нечего добавлять */ }
  }
  return out;
}

const classifier = classifierPath();

// Сравнение по смыслу: временный вход собирается на время вызова, чтобы не
// затирать сами файлы разрешений.
function semanticRepeats(base) {
  if (!classifierAvailable(classifier)) return '';
  const merged = `${store}.compare-input`;
  try {
    fs.writeFileSync(merged, base);
  } catch {
    return '';
  }
  const verdict = classify(classifier, 'delta', [merged, plan], '');
  try {
    fs.rmSync(merged, { force: true });
  } catch { /* временный файл переживёт прогон, это не влияет на вердикт */ }
  return verdict.startsWith('REPEATS:') ? verdict.slice('REPEATS:'.length) : '';
}

let approved = '';
try {
  approved = fs.readFileSync(store, 'utf8');
} catch { /* одобренных планов ещё не было */ }

if (!approved.trim()) {
  // Одобренных планов нет, но семантические разрешения есть: повтор
  // разрешённого вопросом или указанием ловится сравнением по смыслу — иначе
  // показ такого плана шёл бы как первый план сессии.
  const base = approvalsText();
  if (base) {
    const repeats = semanticRepeats(base);
    if (repeats) {
      deny(`План повторяет одобренное вопросом-разрешением или прямым указанием Влада:${repeats}. Уже разрешено — выполняй сразу, показывать план не нужно. Аварийный выключатель — PLAN_DELTA=off.`);
    }
  }
  process.exit(0);
}

const approvedHashes = approved.split('\n').map((h) => h.trim()).filter(Boolean);
const repeated = now.filter((u) => approvedHashes.includes(u.hash));

if (repeated.length === now.length) process.exit(0); // перепоказ того же плана целиком

// Хеш ловит только ДОСЛОВНЫЙ повтор: переформулированный одобренный юнит даёт
// ноль совпадений и раньше проезжал полным перепоказом. Сравнение по смыслу
// закрывает это; его недоступность возвращает к хеш-поведению — ложный отказ
// дороже пропуска.
if (repeated.length === 0) {
  let snapshot = '';
  try {
    snapshot = fs.readFileSync(planDeltaSnapshot(), 'utf8');
  } catch { /* снимка нет — сравнивать не с чем */ }
  if (snapshot) {
    const repeats = semanticRepeats(snapshot + approvalsText());
    if (repeats) {
      deny(`План повторяет уже одобренные юниты по смыслу:${repeats}. Одобренное повторно не показывается — оставь только изменившееся с прошлого одобрения, а изменённый юнит пометь ревизией с причиной. Аварийный выключатель — PLAN_DELTA=off.`);
    }
  }
  process.exit(0); // чистая дельта
}

const names = repeated.map((u) => `«${u.title}» `).join('');
deny(`План повторяет уже одобренные юниты: ${names}. Одобренное повторно не показывается — оставь только изменившееся с прошлого одобрения, а изменённый юнит пометь ревизией с причиной. Аварийный выключатель — PLAN_DELTA=off.`);
