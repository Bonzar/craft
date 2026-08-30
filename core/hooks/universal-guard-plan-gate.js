#!/usr/bin/env node
// PreToolUse plan-gate: любой вызов инструмента по умолчанию закрыт, если он
// явно не доказан как чтение или разрешённая операция над состоянием самой
// сессии. Остальное открывает РЕЕСТР ОДОБРЕННОГО, а не имя инструмента или путь.
//
// Одна сверка на все поверхности: правка вместе с реестром уходит модели, и та
// отвечает одним из пяти исходов — запрещено записью, разрешено поверх запрета,
// покрыта задачей, черновое, не покрыта. Пути из «где:» остаются подсказкой в
// рендере, но основанием для отказа быть перестали: план вида «переименовать во
// всех местах использования» файлов не перечисляет.
//
// Поверхности:
//   - file.mutate — правки файлов где угодно, кроме
//     эфемерного (планы, tmp/scratchpad, служебное состояние харнессов) и
//     игнорируемого гитом вне системных зон адаптеров
//     файлы (settings.local.json, кэш предодобренной зоны), их игнор-лазейка
//     открывала бы без плана;
//   - command.run — общий разбор объединяет файловые цели, точные мутаторы
//     дерева и доказательство чтения. Временная цель оправдывает только свою
//     мутацию; неизвестная или безадресная мутация остаётся закрытой;
//   - data.mutate — сверяется так же; отдельно и раньше сверки проходит
//     предодобренная зона (exempt-scope, напр. «Продукты»).
//   - любой прочий, в том числе ещё неизвестный системе инструмент, — тоже
//     идёт в сверку. Белый список состоит только из доказанного чтения и
//     операций над ходом самой сессии.
//
// Приём материала в реестр идёт фоном, и сверка ЖДЁТ его конца: правка, которую
// Влад только что разрешил репликой, иначе упёрлась бы в гейт.
//
// Выходов у гейта нет: ни автономного прогона, ни режима харнесса. Снять
// проверки можно только тапом Влада — рубильником, который живёт записью в
// реестре и гаснет со сменой сессии.
//
// Неопознанная команда или конструкция не считается безопасной по умолчанию:
// write-intent возвращает unknown, и тот проходит те же правила plan-gate.
import fs from 'node:fs';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { readEvent } from './lib/event.js';
import { planRequired } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { lastInputTrace, exemptScopeFile, approvalRegistry } from './lib/paths.js';
import {
  waitForParsing, readRegistry, render, switchAt, appendLog,
} from './lib/registry.js';
import { classify, classifierPath } from './lib/classifier.js';
import { sessionAnchor } from './lib/paths.js';
import { writeIntent, requiresPlanGate } from '../plan-gate/write-intent.js';
import { runPlanGateRules } from '../plan-gate/rules/index.js';
import { sha256 } from './lib/hash.js';
import { canonicalPatchChanges } from '../contracts/action.mjs';
import { parseCoverVerdict, parsePreflightVerdict } from '../classifier/verdict.mjs';

const { raw, event, action, route, input } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

// Режимов харнесса гейт больше не слушает — ни bypassPermissions, ни acceptEdits.
// Снять проверки можно, но только тапом Влада, и след этого живёт записью в
// реестре: переменная окружения и режим клиента такого следа не оставляют.
// Автономный прогон тоже не выход: его задание ложится в реестр целью, и правки
// рутины сверяются с ним наравне со всеми.

// Отладочный след последнего входа (эфемерный): по нему проверяются факты о
// составе hook-входа (напр. поле permission_mode) без правки харнесса.
try {
  fs.writeFileSync(lastInputTrace('plan-gate'), raw);
} catch { /* след не записался — на решение гейта это не влияет */ }

const classifier = classifierPath();

// Deterministic normalization may prove a call to be read-only. Every other
// call is first classified by the model before any rule can allow it. The model
// returns only a typed decision; core validates it against the deterministic
// effect, and only deterministic code may consult or mutate the registry.
const intent = writeIntent(action);
if (!requiresPlanGate(intent)) process.exit(0);

const preflightInput = JSON.stringify({ action, intent });
const preflightRaw = classify(classifier, 'preflight', [], preflightInput);
const preflight = parsePreflightVerdict(preflightRaw);

if (preflight?.kind === 'DENY') {
  planRequired(`Заблокировано план-гейтом: предварительная модельная проверка отказала — ${preflight.detail}`, {
    blockedAction: action,
    originalIntent: event.originalIntent || '',
    transitionId: sha256(`${event.sessionId || ''}\n${raw}`),
  });
}

const expectedPreflight = {
  session: 'ALLOW_SESSION',
  ephemeral: 'ALLOW_EPHEMERAL',
  world: 'CHECK_REGISTRY',
  unknown: 'CHECK_REGISTRY',
}[intent.effect];

if (!preflight || preflight.kind !== expectedPreflight) {
  planRequired('Заблокировано план-гейтом: обязательная предварительная модельная проверка недоступна, невалидна или противоречит вычисленному эффекту. Конечный результат остаётся fail-closed.', {
    blockedAction: action,
    originalIntent: event.originalIntent || '',
    transitionId: sha256(`${event.sessionId || ''}\n${raw}`),
  });
}

if (preflight.allowing) process.exit(0);

// Предел описания правки. Раньше он был 2000 байт на заменяемый текст и 4000 на
// новый и стоял из-за потолка в 128 КБ на один аргумент командной строки:
// описание уходило модели в argv. Промпт давно идёт на СТАНДАРТНЫЙ ВВОД, потолка
// нет, а обрез остался и резал живую правку посреди кода — сверка видела обрубок
// и отказывала «текст обрезан на середине».
//
// Число здесь — страховка от бинарного мусора (случайный дамп, картинка), а не
// бюджет: правка такого размера в промпт не помещается по смыслу, а не по форме.
const DESC_LIMIT = 200000;

// Срез по БАЙТАМ с хвостовым переводом строки, как его делал bash: подстановка
// команды добавляла к тексту перевод строки, резала head -c и снимала хвостовые
// переводы обратно. Возвращается буфер — срез посреди многобайтного символа
// обязан дать те же байты, что давал шелл.
function headBytes(text, limit) {
  const cut = Buffer.from(`${text}\n`, 'utf8').subarray(0, limit);
  let end = cut.length;
  while (end > 0 && cut[end - 1] === 0x0a) end -= 1;
  return cut.subarray(0, end);
}

// Разрешающие исходы сверки. Остальное — отказ либо неответ.
const allows = (verdict) => parseCoverVerdict(verdict)?.allowing === true;

// Сверка — вызов модели, и на пограничной правке она отвечает по-разному на
// одном и том же входе: замер пяти прогонов подряд дал четыре отказа и одно
// «покрыта». Цена разброса ложится на Влада — он повторяет разрешение по два-три
// раза, пока не повезёт.
//
// Поэтому отказ не окончателен с первого раза: он переспрашивается, а разошлись
// ответы — решает третий голос. Проход остаётся проходом сразу: лишние вызовы
// тратятся только там, где иначе Влад теряет минуты. Отказ возвращается ПЕРВЫЙ
// из полученных — его причина уже написана про эту правку.
function steadyVerdict(view, desc) {
  const first = classify(classifier, 'cover', [view], desc);
  if (allows(first)) return first;

  const second = classify(classifier, 'cover', [view], desc);
  const agree = allows(first) === allows(second);
  if (agree) return first;

  const third = classify(classifier, 'cover', [view], desc);
  if (allows(third)) return allows(second) ? second : third;
  return allows(first) ? second : first;
}

function tempFile(prefix) {
  const dir = process.env.TMPDIR || '/tmp';
  return path.join(dir, `${prefix}.${randomBytes(3).toString('hex')}`);
}

// --- Сверка по реестру одобренного -------------------------------------------
// Одна сверка вместо трёх веток. Прежние спрашивали каждая про своё — план,
// окно разрешений, времянка, — и правка сверялась то с одним, то с другим.
//
// Пять исходов: запрещено записью, разрешено поверх запрета, покрыта задачей,
// черновое, не покрыта. Запрет проверяется раньше покрытия, а более позднее
// явное разрешение бьёт запрет.
//
// Нет решения — отказ, всегда: ни падение проверки, ни недоступность модели, ни
// смерть самого хука проходом не становятся. Мягкой деградации к путь-матчу
// больше нет — пути перестали быть основанием для решения.
function coverCheck(desc) {
  const registry = approvalRegistry();
  let cachedGoals;

  const goals = () => {
    if (cachedGoals !== undefined) return cachedGoals;
    waitForParsing(registry);
    cachedGoals = readRegistry(registry);
    return cachedGoals;
  };

  const registryDecision = (approved) => {
    if (!approved.length) {
      return { decision: 'deny', rule: 'registry', reason: 'Заблокировано план-гейтом: одобренного нет — реестр пуст. Покажи план и получи ок Влада, либо задай предметный вопрос-разрешение.' };
    }

    const view = tempFile('registry-view');
    try {
      fs.writeFileSync(view, render(approved));
    } catch {
      return { decision: 'deny', rule: 'registry', reason: 'Заблокировано план-гейтом: реестр одобренного не читается, сверить вызов не с чем.' };
    }
    const verdict = steadyVerdict(view, desc);
    try { fs.rmSync(view, { force: true }); } catch { /* temporary view remains */ }

    const parsed = parseCoverVerdict(verdict);
    if (parsed?.kind === 'OVERRIDE' || parsed?.kind === 'COVERED') return { decision: 'allow', rule: 'registry' };
    if (parsed?.kind === 'FORBIDDEN') {
      return { decision: 'deny', rule: 'registry', reason: `Заблокировано план-гейтом: это запрещено твоей же записью —${verdict.slice('FORBIDDEN:'.length)}. Пути дальше: сними запрет прямо в диалоге, либо покажи план с этой целью.` };
    }
    if (parsed?.kind === 'DRAFT') {
      if (approved.some((goal) => goal.source === 'plan')) return { decision: 'allow', rule: 'registry' };
      return { decision: 'deny', rule: 'registry', reason: 'Заблокировано план-гейтом: вызов выглядит черновым, но одобренного плана в реестре нет — чернового без работы не бывает.' };
    }
    if (parsed?.kind === 'UNCOVERED') {
      return { decision: 'deny', rule: 'registry', reason: `Заблокировано план-гейтом: одобренное этого не покрывает —${verdict.slice('UNCOVERED:'.length)}. Пути дальше: покажи план с этой целью или дай прямое разрешение в диалоге.` };
    }
    return { decision: 'deny', rule: 'registry', reason: 'Заблокировано план-гейтом: сверка не дала решения, а без него вызов не идёт. Конечный результат остаётся fail-closed.' };
  };

  const result = runPlanGateRules({
    intent,
    anchorFile: sessionAnchor(),
    exemptions: {
      anchor: Boolean(process.env.CRAFT_AUTONOMOUS || process.env.CRAFT_EVAL || process.env.CRAFT_NESTED_CALL),
    },
    goals,
    switchAt,
    appendSwitchLog: (at) => appendLog(registry, at, `без сверки · ${String(desc).split('\n')[0].slice(0, 120)}`),
    scopeFile: exemptScopeFile(),
    registryDecision,
  });

  if (result.decision === 'deny') planRequired(result.reason, {
    blockedAction: action,
    originalIntent: event.originalIntent || '',
    transitionId: sha256(`${event.sessionId || ''}\n${raw}`),
  });
}

// --- Single-target file mutations -------------------------------------------
if (route === 'file.mutate') {
  const fp = typeof input.target === 'string' ? input.target : '';
  if (!fp) coverCheck(Buffer.from('действие: file.mutate\nцель: неизвестна', 'utf8'));

  // The adapter folds native edit variants into one canonical text delta.
  const edits = Array.isArray(input.edits) ? input.edits : [];
  const joined = (key) => edits.map((e) => (e && e[key]) || '').join('\n---\n');
  const oldText = input.previousText || joined('previousText');
  const newText = input.newText || joined('newText');

  const desc = Buffer.concat([
    Buffer.from(`действие: file.mutate\nфайл: ${fp}\nзаменяемый текст:\n`, 'utf8'),
    headBytes(oldText, DESC_LIMIT),
    Buffer.from('\nновый текст:\n', 'utf8'),
    headBytes(newText, DESC_LIMIT),
  ]);

  // Абсолютный путь к цели внутри текущего репо матчится и по репо-относительной
  // записи плана: строки «- где:» пишутся от корня репозитория.
  coverCheck(desc);
  process.exit(0);
}

// A multi-file patch tool sends one patch containing one or more changes. Gate the complete
// material once, but only after ephemeral/build targets have been removed.
if (route === 'file.patch') {
  // writeIntent already proved that at least one source or destination is
  // permanent. Keep the complete patch in the classifier material: filtering
  // by source alone would let an ephemeral file move into a protected target.
  const changes = canonicalPatchChanges(input);
  if (!changes?.length) {
    coverCheck(Buffer.from('действие: file.patch\nцель и материал: неизвестны', 'utf8'));
    process.exit(0);
  }
  const desc = changes.map((change) => [
    `действие: file.patch\nоперация: ${change.kind}\nфайл: ${change.file}${change.destination ? `\nперемещение в: ${change.destination}` : ''}\nзаменяемый текст:`,
    headBytes(change.oldText, DESC_LIMIT).toString(),
    'новый текст:',
    headBytes(change.newText, DESC_LIMIT).toString(),
  ].join('\n')).join('\n---\n');
  coverCheck(Buffer.from(desc, 'utf8'));
  process.exit(0);
}

// --- Command calls ------------------------------------------------------------
if (route === 'command.run') {
  const cmd = input.command || '';
  const bdesc = Buffer.concat([
    Buffer.from('действие: command.run\nкоманда или поток:\n', 'utf8'),
    headBytes(cmd || input.streamInput || '<неограниченный транспорт>', DESC_LIMIT),
  ]);
  coverCheck(bdesc);
  process.exit(0);
}

// --- Прочие инструменты ------------------------------------------------------
// Всё, про что не видно, что оно только читает: чужие MCP-серверы, подагенты,
// которые правят, и то, чего сегодня ещё нет. Сверяется тем же вопросом —
// описанием служит имя инструмента и его вход.
if (route !== 'data.mutate') {
  const odesc = Buffer.concat([
    Buffer.from(`действие: ${route}\nвход:\n`, 'utf8'),
    headBytes(JSON.stringify(input), DESC_LIMIT),
  ]);
  coverCheck(odesc);
  process.exit(0);
}

// --- Database writes ---------------------------------------------------------
const craftCmd = input.command || '';
const cdesc = Buffer.concat([
  Buffer.from('действие: data.mutate\nкоманда:\n', 'utf8'),
  headBytes(craftCmd, DESC_LIMIT),
]);
// Запись сверяется тем же вопросом, что правки файлов и команд: решает
// реестр одобренного, а не совпадение идентификаторов блоков со списком целей.
coverCheck(cdesc);
