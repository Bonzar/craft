#!/usr/bin/env node
// Отмечает, что plan-critic ОТРАБОТАЛ, и по какой версии плана. Отметка — хеш файла
// плана; гейт (universal-guard-plan-critic) сверяет его с текущим содержимым, поэтому
// переписанный после обкатки план отметку не наследует.
//
// Две роли в одном файле, роль по имени события:
//   PostToolUse    — подагент запущен. Ответ инструмента при фоновом запуске — расписка о
//                    запуске, а не вердикт, поэтому отмечать здесь нельзя: убитый или
//                    упавший критик оставлял бы действительную отметку. Запоминаем
//                    ОЖИДАНИЕ: идентификатор подагента из расписки и хеш плана на этот
//                    момент. Расписки нет (подагент отработал синхронно и вернул текст) —
//                    отмечаем сразу, вердикт на руках.
//   UserPromptSubmit — уведомление о завершении задачи. Тот же идентификатор и успешный
//                    статус превращают ожидание в отметку; иной статус ожидание снимает.
//
// Веер, запущенный одним Workflow (tools/plan-critic-fan.sh), идёт тем же путём:
// запуск оставляет ожидание, уведомление о завершении — отметку. Идентификатор из
// расписки Workflow не опознан — ожидания нет, отметку по завершению пишет сам
// скрипт веера (подкоманда mark, идемпотентна).
//
// Хеш берётся в момент ЗАПУСКА: критик читал именно ту версию плана. Правка плана после
// запуска отметку обесценит сама — гейт сверяет её с текущим файлом.
//
// Различитель подагента — subagent_type, и отбираются только роли, ПЕЧАТАЮЩИЕ вердикт:
// сводящий веера (plan-critic-verdict) и одиночный критик всего плана (plan-critic). Юнитные
// критики и критик швов вердикта не печатают и события не отмечают: счётчик ниже считает
// круги обкатки, а не запуски агентов, иначе один веер из нескольких критиков набивал бы
// плато за первый же круг и гейт переставал бы держать показ. Без различителя вовсе гейт
// обходился бы запуском любого подагента.
//
// Fail quiet: не смог посчитать хеш — отметки нет, гейт просто не пропустит.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { sha256File } from './lib/hash.js';
import {
  planCriticMarker, planCriticPending, planCriticRuns, planCriticRound, planFileMarker,
} from './lib/paths.js';

const { raw, event, tool, name, input, response, prompt } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

const marker = planCriticMarker();
const pending = planCriticPending();
const runs = planCriticRuns();
const round = planCriticRound();
const eventName = name || 'PostToolUse';

// Счётчик завершённых прогонов критика — машинное «Плато»: гейт по нему пропускает показ,
// когда обкатка перестала двигать план. Считается ПРОГОН, а не версия файла: правка по
// замечаниям меняет хеш, и счёт по хешу всегда был бы единица. Инкремент идёт всюду, где
// ставится отметка, — иначе синхронный вердикт плато не набирает.
function bumpRuns() {
  let n = 0;
  try {
    const current = fs.readFileSync(runs, 'utf8').trim();
    if (/^[0-9]+$/.test(current)) n = Number(current);
  } catch { /* счётчика ещё нет */ }
  try {
    fs.writeFileSync(runs, `${n + 1}\n`);
  } catch { /* не записалось — плато наберётся позже */ }
}

// Последнее совпадение регулярки в тексте — так же выбирал жадный префикс `.*` у sed.
function lastMatch(text, re) {
  let found = '';
  for (const line of text.split('\n')) {
    const all = [...line.matchAll(new RegExp(re, 'g'))];
    if (all.length) { found = all[all.length - 1][1]; break; }
  }
  return found;
}

// Вердикт критика — машиночитаемая последняя строка его ответа. Он и разрывает
// самоинвалидацию: «блокеров нет» пускает показ, даже если план после обкатки правился,
// то есть шероховатость больше не стоит целого круга. Текст вердикта доступен в обоих
// путях — синхронный ответ подагента и поле результата в уведомлении о завершении.
// Не распознан — пусто: гейт тогда судит по хешу, как раньше.
//
// Читается ТОЛЬКО последняя непустая строка: критик обязан цитировать улики, и поиск по
// всему тексту принимал за вердикт процитированную строку чужого плана — обкатка плана,
// где такая строка есть, выдавала отметку по улике, а не по решению критика.
//
// Переносы восстанавливаются: у синхронного пути ответ приходит текстом, и «\n» в нём
// экранированы — без замены весь ответ был бы одной строкой и правка ничего не меняла.
function verdictOf(text) {
  const lines = text.split('\\n').join('\n').split('\n').filter((l) => /\S/.test(l));
  const last = lines.length ? lines[lines.length - 1] : '';
  if (last.includes('Вердикт: блокеров нет')) return 'noblockers';
  if (last.includes('Вердикт: есть блокеры')) return 'blockers';
  return '';
}

// Текст ответа критика из промпта уведомления: последняя строка самого промпта — всегда
// закрывающий тег, вердикта в ней не бывает. Тега результата нет — пусто, и вердикт не
// распознаётся вовсе.
function resultOf(text) {
  const at = text.indexOf('<result>');
  if (at < 0) return '';
  const rest = text.slice(at + '<result>'.length);
  const end = rest.indexOf('</result>');
  return end < 0 ? rest : rest.slice(0, end);
}

// Отметка — «хеш<таб>вердикт». Вердикта нет — остаётся голый хеш, как было: старые
// отметки и фикстуры читаются тем же первым полем.
function putMark(hash, verdict) {
  try {
    fs.writeFileSync(marker, verdict ? `${hash}\t${verdict}\n` : `${hash}\n`);
  } catch { /* не записалось — гейт просто не пропустит показ */ }
}

function planPath() {
  if (process.env.CRAFT_PLAN_FILE) return process.env.CRAFT_PLAN_FILE;
  try {
    return fs.readFileSync(planFileMarker(), 'utf8').replace(/\n+$/, '');
  } catch {
    return '';
  }
}

if (eventName === 'UserPromptSubmit') {
  if (!prompt.includes('<task-notification>')) process.exit(0);
  const id = lastMatch(prompt, /<task-id>([^<]*)<\/task-id>/.source);
  if (!id) process.exit(0);

  let waiting = [];
  try {
    waiting = fs.readFileSync(pending, 'utf8').split('\n');
  } catch { /* ожиданий нет */ }
  const mine = waiting.find((line) => line.startsWith(`${id}\t`));
  const hash = mine ? mine.split('\t')[1] : '';
  if (!hash) process.exit(0);

  // Ожидание закрыто в любом исходе: повторное уведомление о той же задаче отметку не
  // переставит, а незавершённый критик её не получит вовсе. Пустой остаток файл НЕ
  // заменяет — так вело себя и `grep -v … && mv`, у которого пустой вывод отменял mv.
  const rest = waiting.filter((line) => line !== '' && !line.startsWith(`${id}\t`));
  try {
    fs.writeFileSync(`${pending}.tmp`, rest.length ? `${rest.join('\n')}\n` : '');
    if (rest.length) fs.renameSync(`${pending}.tmp`, pending);
  } catch { /* ожидание осталось — повторное уведомление отметку не переставит */ }

  if (!prompt.includes('<status>completed</status>')) process.exit(0);
  putMark(hash, verdictOf(resultOf(prompt)));
  bumpRuns();
  process.exit(0);
}

let role = input.subagent_type || '';
// Веер, запущенный одним Workflow (tools/plan-critic-fan.sh): событий Task|Agent
// на его внутренних критиков не приходит, поэтому запуск распознаётся по самому
// Workflow-вызову — подстрока plan-critic-fan в пути или meta скрипта. Круг
// веера — одна отметка, как у сводящего.
if (!role && tool === 'Workflow') {
  const source = `${input.scriptPath || ''}\n${(input.script || '').slice(0, 600)}`;
  if (source.includes('plan-critic-fan')) role = 'workflow-fan';
}
const ROLES = ['plan-critic', 'plan-critic-verdict', 'plan-critic-unit', 'plan-critic-seams', 'workflow-fan'];
if (!ROLES.includes(role)) process.exit(0);

const plan = planPath();
if (!plan) process.exit(0);
let hash = sha256File(plan);
if (!hash) process.exit(0);

// Веерные роли отметки не ставят и счётчик не крутят — они лишь запоминают ВЕРСИЮ плана,
// которую читали. Первый критик круга и задаёт эту версию: сводящий план не читает вовсе,
// и без такой памяти его отметка вставала бы на текущий файл — то есть заверяла бы версию,
// которой не видел ни один критик, если план правился между веером и сводящим. С памятью
// круга правка между ними ломает сверку гейта, как и должна.
if (role === 'plan-critic-unit' || role === 'plan-critic-seams') {
  let known = '';
  try {
    known = fs.readFileSync(round, 'utf8');
  } catch { /* памяти круга ещё нет */ }
  if (!known) {
    try {
      fs.writeFileSync(round, `${hash}\n`);
    } catch { /* не записалось — сводящий отметит текущую версию */ }
  }
  process.exit(0);
}

// Роль, печатающая вердикт: отметка идёт на версию круга, если веер её запомнил, и на
// текущую — если критик работал одиночкой. Память круга снимается тут же: следующая
// обкатка начинается с чистого листа.
let roundHash = '';
try {
  roundHash = fs.readFileSync(round, 'utf8').replace(/\n+$/, '');
} catch { /* памяти круга нет */ }
if (roundHash) hash = roundHash;
try {
  fs.rmSync(round, { force: true });
} catch { /* нечего снимать */ }

const resp = response === undefined ? 'null'
  : (typeof response === 'string' ? response : JSON.stringify(response));
let id = lastMatch(resp, /agentId: ([A-Za-z0-9_-]*)/.source);
// У Workflow идентификатор в расписке зовётся иначе: сперва явный task-id,
// потом runId (wf_…) — уведомление о завершении принесёт один из них.
if (!id && role === 'workflow-fan') {
  id = lastMatch(resp, /[Tt]ask[-_ ]?[Ii][Dd][^A-Za-z0-9_-]{1,3}([A-Za-z0-9_-]{4,})/.source);
  if (!id) {
    const wf = resp.match(/wf_[a-z0-9-]{6,}/);
    if (wf) id = wf[0];
  }
}

if (id) {
  try {
    fs.appendFileSync(pending, `${id}\t${hash}\n`);
  } catch { /* ожидание не записалось — отметки по завершению не будет */ }
} else if (role !== 'workflow-fan') {
  // Расписка Workflow вердикта не несёт никогда: без опознанного идентификатора
  // отметки нет — её по завершению пишет сам скрипт веера (подкоманда mark).
  putMark(hash, verdictOf(resp));
  bumpRuns();
}
