#!/usr/bin/env node
// PreToolUse на ExitPlanMode: не показывать Владу то, что он уже одобрял.
//
// Сравнение идёт ТОЛЬКО по смыслу. Хешей юнитов и снимка последнего плана больше
// нет: хеш ловил лишь дословный повтор, и переформулированный кусок проезжал мимо
// него как новый — то есть механизм не делал того, ради чего стоял.
//
// Вход сравнения — ВСЁ одобренное этой сессии: цели любого источника, планом,
// кнопкой или репликой, сколько бы планов назад это ни было. Принятое однажды в
// план не возвращается. Надгробия выброшены: текста у них не осталось, и
// сравнивать новый план с голыми заголовками значит тихо перестать ловить повторы.
//
// Три исхода: повторяет всё — это перепоказ того же плана, он законен и проходит
// молча; повторяет часть — отказ со списком; не повторяет ничего — чистая дельта.
//
// Одобренного ещё нет — показ идёт молча, сравнивать не с чем. Одобренное есть, а
// решения нет — показ НЕ идёт: раньше этот случай страховали хеши, теперь
// страховать нечем, а молчаливый пропуск означал бы, что Влад снова видит уже
// одобренное.
//
// Аварийный выключатель — PLAN_DELTA=off.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { planFileMarker, approvalRegistry } from './lib/paths.js';
import { classify, classifierPath } from './lib/classifier.js';
import { readRegistry, render, waitForParsing } from './lib/registry.js';

if (process.env.PLAN_DELTA === 'off') process.exit(0);

const { raw, event, tool } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
if (tool !== 'ExitPlanMode') process.exit(0);

function planPath() {
  if (process.env.CRAFT_PLAN_FILE) return process.env.CRAFT_PLAN_FILE;
  try {
    return fs.readFileSync(planFileMarker(), 'utf8').trim();
  } catch {
    return '';
  }
}

const plan = planPath();
try {
  fs.accessSync(plan, fs.constants.R_OK);
} catch {
  process.exit(0); // файла плана нет — сравнивать нечего
}

const classifier = classifierPath();

// Вход сравнения: всё одобренное сессии, без надгробий.
function approvedText() {
  // Дельта ждёт разбора наравне со сверкой: требование сформулировано как
  // принцип, а не про одну функцию. Иначе план, покрытый только что данным
  // согласием, показывался бы снова.
  const registry = approvalRegistry();
  waitForParsing(registry);
  const goals = readRegistry(registry).filter((goal) => goal.state !== 'tombstone');
  return goals.length ? render(goals) : '';
}

// Временный вход собирается на время вызова: сами файлы согласия не трогаются.
function compare(approved) {
  const input = `${planFileMarker()}.delta-input`;
  try {
    fs.writeFileSync(input, approved);
  } catch {
    return 'UNAVAILABLE';
  }
  const verdict = classify(classifier, 'delta', [input, plan], '');
  try {
    fs.rmSync(input, { force: true });
  } catch { /* временный вход переживёт прогон, на вердикт это не влияет */ }
  return verdict;
}

const approved = approvedText();
if (!approved) process.exit(0);

const verdict = compare(approved);

// Перепоказ того же плана целиком законен: правило прямо велит показывать заново
// план, в котором нового нет вовсе.
if (verdict === 'REPEATSALL' || verdict === 'CLEAN') process.exit(0);

if (verdict.startsWith('REPEATS:')) {
  deny(`План повторяет уже одобренное:${verdict.slice('REPEATS:'.length)}. Одобренное повторно не показывается — оставь только изменившееся, а изменённый кусок пометь ревизией с причиной. Аварийный выключатель — PLAN_DELTA=off.`);
}

deny('Показ плана остановлен: сравнить его с одобренным не удалось, а без сравнения Влад увидит то, что уже принимал. Повтори показ; если проверка не поднимается — аварийный выключатель PLAN_DELTA=off.');
