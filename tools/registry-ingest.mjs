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
import {
  classifierPath, classify, INGEST_BUDGET_SEC, INGEST_PASSES,
} from '../.claude/hooks/lib/classifier.js';
import {
  readRegistry, upsertGoal, addTasks, render, unmarkParsing, closeTasks, liftBans, landingGoal,
} from '../.claude/hooks/lib/registry.js';
import { currentMetricsLog } from '../.claude/hooks/lib/metrics.js';
import { queueSummary, QUEUE_WAIT_MS } from '../.claude/hooks/lib/metrics-store.js';
// Адаптер хранения выбирает край, а не общая часть.
import * as STORE_ADAPTER from '../.claude/hooks/lib/metrics-store-git.js';

const [, , source, materialFile, registryFile, markId] = process.argv;

// След приёма: файл рядом с реестром, куда ложится ход разбора. Без него провал
// приёма неотличим от того, что приёма не было вовсе — вызов идёт со сброшенным
// выводом и в пустом catch, а помощник проверки ничего не логирует. Ровно
// поэтому пропажу целого плана из реестра заметил Влад, а не система.
//
// Файл лежит в общем /tmp контейнера и читаем всем, кто в него попал: в след
// идёт только ход приёма — номер прохода, размер реестра, исход и размер
// ответа модели. Сам текст ответа в след не пишется.
export function trace(line) {
  if (!registryFile) return;
  try {
    fs.appendFileSync(`${registryFile}.ingest.log`, `${new Date().toISOString()} ${source} ${line}\n`);
  } catch { /* след не записался — работу приёма это не меняет */ }
}

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

// Один проход разбора: реестр отдаётся модели в том же читаемом виде, в каком
// его увидит сверка — отдельный машинный формат разъезжался бы с тем, что видно
// в отказе, — и ответ применяется к файлу.
function pass(material, n, ownFrom) {
  const current = readRegistry(registryFile);
  // Вид кладётся рядом с РЕЕСТРОМ, а не с материалом: материалом у плана служит
  // сам файл плана, и вид ложился бы в каталог планов (в кейсах — в фикстуры,
  // затирая их).
  //
  // Имя — своё на каждый приём и проход: приёмы одной сессии идут параллельно
  // (реплика и следом ответ на кнопку — реестр это прямо допускает), и на общем
  // имени один процесс затирал бы снимок другого, а его уборка сносила бы уже
  // чужой файл — классификатор читал бы не тот реестр или пустоту.
  const view = `${registryFile}.view.${process.pid}.${n}`;
  try {
    fs.writeFileSync(view, render(current));
  } catch { /* вид не записался — модель увидит пустой реестр */ }

  const verdict = classify(classifierPath(), 'ingest', [view, materialFile, source], '', {
    timeoutSec: INGEST_BUDGET_SEC,
  });
  try { fs.rmSync(view, { force: true }); } catch { /* вид переживёт приём */ }
  if (!verdict || verdict === 'UNAVAILABLE') {
    trace(`проход ${n}: целей в реестре ${current.length}, модель недоступна`);
    return 1;
  }

  let answer;
  try {
    answer = JSON.parse(verdict);
  } catch {
    trace(`проход ${n}: целей в реестре ${current.length}, ответ модели неразборен (${verdict.length} символов)`);
    return 1;
  }
  if (!answer || !Array.isArray(answer.add)) {
    trace(`проход ${n}: целей в реестре ${current.length}, ответ модели без списка добавлений`);
    return 1;
  }
  const additions = answer.add;
  trace(`проход ${n}: целей в реестре ${current.length}, ответ модели: добавить ${additions.length}, закрыть ${Array.isArray(answer.close) ? answer.close.length : 0}, снять ${Array.isArray(answer.lift) ? answer.lift.length : 0}`);

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
    // Реестр перечитывается на КАЖДОЙ записи ответа: цель, заведённая
    // предыдущей записью этого же ответа, обязана быть видна следующей —
    // прохода, который раньше дозаводил такие ссылки, больше нет.
    const goals = readRegistry(registryFile);
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

    // Куда приземлить запись, решает ядро реестра: цель адресуется НОМЕРОМ из
    // рендера, а не заголовком (формулировку модель каждый раз пишет свою), и от
    // ПЛАНА слияние не принимается вовсе — у плана всегда своя цель.
    const index = landingGoal(goals, add.goal, source, ownFrom);

    const bodies = cutByAnchors(material, tasks);
    const prepared = tasks.map((t, i) => ({
      title: String(t.title || '').trim() || 'без имени',
      where: Array.isArray(t.where) ? t.where.map(String) : [],
      body: bodies[i],
    }));

    if (index >= 0) {
      addTasks(registryFile, index, prepared);
      continue;
    }
    // Заголовок новой цели — от модели: сперва goal_new, иначе имя первой
    // задачи. Второе не выдумка и не пустая строка: задачи в куске есть всегда
    // (пустой кусок отсеян выше), и имя им дала та же модель по тому же
    // материалу. Из разметки материала заголовок не выводится — у плана таких
    // строк несколько и они про разное, а у реплики их нет вовсе.
    const title = String(add.goal_new || '').trim() || prepared[0].title;
    upsertGoal(registryFile, { title, source, tasks: prepared });
  }
  return 0;
}

function main() {
  if (!materialFile || !registryFile) return 1;
  let material = '';
  try {
    material = fs.readFileSync(materialFile, 'utf8');
  } catch {
    return 1;
  }

  // Число проходов задаёт классификатор (INGEST_PASSES); его же знает хук
  // одобрения — свой срок он выводит из него и бюджета прохода.
  // Граница своих целей: всё, что лежало в реестре ДО этого приёма, для плана
  // чужое; заведённое этим приёмом — своё.
  const ownFrom = readRegistry(registryFile).length;

  // Проход сейчас один (INGEST_PASSES = 1), и цикл написан под несколько:
  // сорвавшийся проход роняет приём только если он ПЕРВЫЙ — на втором и дальше
  // в реестре уже что-то лежит, и терять это из-за неответа модели незачем.
  let done = 0;
  for (let n = 1; n <= INGEST_PASSES; n += 1) {
    const code = pass(material, n, ownFrom);
    if (code !== 0) return done === 0 ? code : 0;
    done += 1;
  }
  return 0;
}

// Приём кончается ПОЗЖЕ последнего Stop сессии, и его вызов модели попадает в
// сводку, которую работник хранения уже увёз. Возврат в очередь делается здесь,
// на краю: журнал про хранение не знает, а знать, что этот процесс последний,
// может только сам процесс. Сводки нет — приём шёл до первого Stop, её сложит
// он сам.
//
// Приём ПЛАНА — исключение: он идёт внутри хода, синхронным вызовом из хука
// одобрения, и подпроцесс git с ожиданием лока там были бы платой хода за
// работу, которую следующий Stop сделает сам.
function requeueSummary() {
  if (source === 'plan') return;
  const log = currentMetricsLog();
  if (!log) return;
  let summary;
  try {
    summary = JSON.parse(fs.readFileSync(`${log}.summary.json`, 'utf8'));
  } catch {
    return; // сводки ещё нет — возвращать нечего
  }
  // Срок ожидания лока называет КРАЙ: приём идёт следом за ходом, и ждать он
  // может только столько же, сколько ждут метрики внутри хода.
  if (summary && summary.sid) queueSummary(summary, log, STORE_ADAPTER, { waitMs: QUEUE_WAIT_MS });
}

let code = 1;
try {
  code = main();
} finally {
  if (markId) unmarkParsing(path.join(`${registryFile}.parsing`, markId));
  requeueSummary();
}
process.exit(code);
