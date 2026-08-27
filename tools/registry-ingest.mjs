#!/usr/bin/env node
// Приём материала в реестр одобренного: план, ответ на кнопочный вопрос или
// реплика Влада — все три идут одним путём.
//
// Разбор делает модель: она решает, сколько тут целей, сколько задач и как они
// вложены. Форма документа для этого не критерий — план каждый раз свёрстан
// по-своему, а заголовки не обещают, что за ними одна задача. Здесь же
// семантическая дедупликация: материал про уже одобренную цель ложится новой
// задачей под неё, а совпавшее по смыслу не добавляется вовсе.
//
// Тело задачи вырезает КОД по якорю — дословной первой строке её куска. Так
// цитата приходит из материала, а не пересказывается моделью, и ответ модели
// остаётся коротким.
//
// Тем же вызовом приходят и ЗАКРЫТИЯ — задачи, про которые по логу цели видно,
// что работа сделана. Принимаются они только от одобренного плана: он и есть
// граница работы, а реплика ей не является.
//
// Запускается фоном для реплики и кнопки (ход Влада не ждёт модель) и
// синхронно для плана (правки идут сразу за одобрением). Пока разбор идёт, у
// реестра стоит метка, и сверка правки её дожидается: сверять по недособранному
// реестру значит отклонять только что разрешённое.
import fs from 'node:fs';
import path from 'node:path';
import { classifierPath, classify } from '../.claude/hooks/lib/classifier.js';
import {
  readRegistry, upsertGoal, addTasks, render, unmarkParsing, closeTasks, liftBans,
} from '../.claude/hooks/lib/registry.js';

const [, , source, materialFile, registryFile, markId] = process.argv;

function cutByAnchors(text, tasks) {
  // Куски материала между якорями: якорь — дословная первая строка задачи.
  // Якоря нет или он не нашёлся — телом становится весь материал: потерять
  // тело хуже, чем взять его шире.
  const lines = text.split('\n');
  const at = tasks.map((t) => {
    const anchor = (t.anchor || '').trim();
    if (!anchor) return -1;
    return lines.findIndex((ln) => ln.trim() === anchor);
  });
  return tasks.map((t, i) => {
    const from = at[i];
    if (from < 0) return text.trim();
    const nextStarts = at.slice(i + 1).filter((x) => x > from);
    const to = nextStarts.length ? Math.min(...nextStarts) : lines.length;
    return lines.slice(from, to).join('\n').trim();
  });
}

function main() {
  if (!materialFile || !registryFile) return 1;
  let material = '';
  try {
    material = fs.readFileSync(materialFile, 'utf8');
  } catch {
    return 1;
  }

  // Реестр отдаётся модели в том же читаемом виде, в каком его увидит сверка:
  // отдельный машинный формат для неё разъезжался бы с тем, что видно в отказе.
  const current = readRegistry(registryFile);
  const view = path.join(path.dirname(materialFile), 'registry-view.txt');
  try {
    fs.writeFileSync(view, render(current));
  } catch { /* вид не записался — модель увидит пустой реестр */ }

  const verdict = classify(classifierPath(), 'ingest', [view, materialFile, source], '');
  if (!verdict || verdict === 'UNAVAILABLE') return 1;

  let answer;
  try {
    answer = JSON.parse(verdict);
  } catch {
    return 1;
  }
  if (!answer || !Array.isArray(answer.add)) return 1;
  const additions = answer.add;

  // Закрытия принимаются ТОЛЬКО от одобренного плана: новый план и есть граница
  // работы, а реплика ей не является — «продолжай» и «работай» значат, что
  // работа идёт. Проверка стоит здесь, а не только в промпте: суждение модели
  // на этом уже мерили, и оно ошибается примерно в каждом пятом разборе.
  //
  // Закрытие идёт ДО добавления: задачи, заводимые этим же вызовом, попасть под
  // нож не могут даже по ошибке модели.
  if (source === 'plan' && Array.isArray(answer.close) && answer.close.length) {
    closeTasks(registryFile, answer.close.map(String));
  }

  // Снятие запрета принимается от ЛЮБОГО источника, в отличие от закрытия задач:
  // «можно снова .ts» — это решение Влада, и он говорит его репликой или кнопкой
  // так же часто, как планом. Закрытие же спрашивается только у плана, потому
  // что план и есть граница работы.
  if (Array.isArray(answer.lift) && answer.lift.length) {
    liftBans(registryFile, answer.lift.map(String));
  }

  for (const add of additions) {
    const tasks = Array.isArray(add.tasks) ? add.tasks : [];

    // ЗАПРЕТ — запись без задач: работы под ним нет, он лишь очерчивает, чего
    // не делать. Заводится только новой записью: дописать запрет к цели-работе
    // некуда, а вид у записи один.
    if (add.kind === 'ban') {
      const banned = String(add.goal_new || '').trim();
      if (banned) upsertGoal(registryFile, { title: banned, source, kind: 'ban', text: material.trim() });
      continue;
    }

    if (!tasks.length) continue;

    // Существующая цель адресуется НОМЕРОМ из рендера, а не заголовком:
    // формулировку модель каждый раз пишет свою, и сравнение заголовков
    // задвоило бы цель при первом же пересказе.
    const ref = String(add.goal || '').trim();
    const at = /^Ц(\d+)$/i.exec(ref);
    const index = at ? Number(at[1]) - 1 : -1;
    const existing = index >= 0 ? current[index] : undefined;

    const bodies = cutByAnchors(material, tasks);
    const prepared = tasks.map((t, i) => ({
      title: String(t.title || '').trim() || 'без имени',
      where: Array.isArray(t.where) ? t.where.map(String) : [],
      body: bodies[i],
    }));

    // Цель адресуется ПОЗИЦИЕЙ, а не заголовком: заголовки повторяются, и
    // дописывание по имени садилось на первую совпавшую — то есть на чужую цель.
    if (existing) {
      addTasks(registryFile, index, prepared);
      continue;
    }
    const title = String(add.goal_new || '').trim();
    // Ни номера существующей цели, ни заголовка новой — приземлять запись
    // некуда. Молча заводить цель с выдуманным именем нельзя: она открыла бы
    // правки, которых Влад не одобрял.
    if (title) upsertGoal(registryFile, { title, source, tasks: prepared });
  }
  return 0;
}

let code = 1;
try {
  code = main();
} finally {
  if (markId) unmarkParsing(path.join(`${registryFile}.parsing`, markId));
}
process.exit(code);
