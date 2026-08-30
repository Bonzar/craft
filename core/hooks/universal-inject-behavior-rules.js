#!/usr/bin/env node
// SessionStart hook (устанавливается в пользовательский слой): живой инжект
// правил общения с Владом из Craft в сессии ВНЕ craft-репо — код-сессии, чужие
// проекты. Канон правил остаётся в Craft (правится с телефона, действует сразу
// во всех сессиях); никакого коммитнутого кэша — только живое чтение на старте.
//
// В craft-репо не работает: там роутер (включая «Общение с Владом») инжектится
// целиком своим хуком и доставкой через клиентский адаптер.
//
// ДОСТАВКА — ФАЙЛОМ, НЕ ПЕЧАТЬЮ. Вывод SessionStart-хука обрезается на 10 000
// символах, а страница правил общения давно длиннее: печатью терялась её
// последняя треть, причём молча. Поэтому тело уходит в файл-снимок, а его
// подтягивает файл инструкций харнесс-сессии — у этого канала потолка нет
// (проверено живой пробой: вложенный вызов из чужого каталога видит
// импортированный текст). В выводе остаётся только строка-отчёт.
//
// Снимок ОБЩИЙ для всех сессий, поэтому он никогда не пустеет и не сносится:
// перезапись идёт атомарно, уже готовым текстом, и соседняя сессия читает либо
// прежнюю версию, либо новую, но не пустоту. Свежесть показывает метка времени
// внутри снимка, а не его снос: при мёртвой сети прежний текст остаётся, но
// датирован — агент видит, что читает вчерашнее.
//
// Канал не установлен (файл инструкций не ссылается на снимок) — хук печатает
// тело по-старому: молчать нельзя, правила обязательны.
//
// Fail quiet: нет доступа или сети → короткая пометка-директива вместо правил.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { loadEnv } from './lib/env.js';
import { fetchText } from './lib/net.js';
import { utcStamp } from './lib/system.js';

// В craft-репо (там есть свой инжектор роутера) — не дублируем. Снимок при этом
// НЕ трогаем: он общий, и опустошение снесло бы правила у соседней код-сессии.
// Инжектор роутера ищется в каноническом core.
function isCraftRepo(dir) {
  if (!dir) return false;
  return ['craft-inject-router.js', 'craft-inject-router.sh']
    .some((name) => fs.existsSync(path.join(dir, 'core', 'hooks', name)));
}
if (isCraftRepo(process.env.CRAFT_PROJECT_DIR)) process.exit(0);

loadEnv();

const communicationId = process.env.CRAFT_COMMUNICATION_ID || '7485dec3-f1c2-4f17-e88a-72994f772b84';
const instructionFile = process.env.CRAFT_USER_INSTRUCTION_FILE || '';
const snapshot = process.env.CRAFT_BEHAVIOR_SNAPSHOT
  || path.join(os.homedir(), '.craft', 'live', 'behavior-rules.md');
// Печать тела остаётся аварийным путём, и её потолок прежний: вывод капится.
const BUDGET = 9500;

function fallback(why) {
  process.stdout.write(`⚠️ Правила общения с Владом не загружены из Craft (${why}). Они обязательны в любой сессии: при доступном Craft MCP прочитай блок ${communicationId} (blocks get --depth -1) перед содержательными ответами; без Craft — держи минимум: структура вместо полотна, выбор — кнопками, факт отдельно от догадки, ссылки кликабельными.\n`);
  process.exit(0);
}

// Канал жив, только пока импорт снимка стоит в файле инструкций: без него файл
// никто не прочитает, и тело обязано идти печатью.
function channelReady() {
  try {
    return instructionFile !== '' && fs.readFileSync(instructionFile, 'utf8').includes(snapshot);
  } catch {
    return false;
  }
}

// Тестовый шов: прогон кейсов идёт без живого Craft, а проверяемое — куда уходит
// тело и что остаётся в выводе, не сам текст правил. Пустой CRAFT_API_BASE
// снаружи для этого не годится: `.env` перекрывает переданное окружение.
let md;
let stamp;
if (process.env.BEHAVIOR_RULES_TEST_MD) {
  try {
    md = fs.readFileSync(process.env.BEHAVIOR_RULES_TEST_MD, 'utf8').replace(/\n+$/, '');
  } catch {
    fallback('тестовый шов без источника');
  }
  stamp = 'тестовый инжект';
} else {
  const base = (process.env.CRAFT_API_BASE || '').replace(/\/$/, '');
  if (!base) fallback('CRAFT_API_BASE не задан');
  md = await fetchText(`${base}/blocks?id=${communicationId}&maxDepth=-1`);
  if (!md) fallback('сеть/API недоступны');
  md = md.replace(/\n+$/, '');
  stamp = utcStamp();
}

// Правило якоря сессии живёт подстраницей роутера, а роутер в чужие проекты не
// едет — тянем его тем же каналом. Оно участвует в сессионных проверках записи,
// поэтому должно быть в том же тексте правил, чтобы поведение не разваливалось на
// живых сессиях из‑за временного отсутствия источника.
// Не дотянулось — это не повод терять правила общения: их тело уже собрано.
const ANCHOR_OPEN = '=== Craft: «Задача-якорь сессии», живой инжект';
const ANCHOR_CLOSE = '=== конец правила якоря ===';
const anchorId = process.env.CRAFT_ANCHOR_RULE_ID || 'eba151a5-ba0e-f173-3eb3-e4b65a8d95ce';
let anchorMd = '';
if (anchorId && !process.env.BEHAVIOR_RULES_TEST_MD) {
  const base = (process.env.CRAFT_API_BASE || '').replace(/\/$/, '');
  if (base) {
    const fetched = await fetchText(`${base}/blocks?id=${anchorId}&maxDepth=-1`);
    if (fetched) anchorMd = fetched.replace(/\n+$/, '');
  }
}

// Правило якоря не дотянулось, но в прошлом снимке оно есть — переносим оттуда.
// Перезапись снимка телом без якоря выбросила бы последний известный текст
// сессионной логики записи: сессия оставалась бы с отказом и без объяснения,
// почему правило нужно. Протухший текст правила лучше его отсутствия — тем же
// обменом живёт и весь снимок.
function anchorFromSnapshot() {
  let previous = '';
  try {
    previous = fs.readFileSync(snapshot, 'utf8');
  } catch {
    return '';
  }
  const start = previous.indexOf(ANCHOR_OPEN);
  if (start === -1) return '';
  const end = previous.indexOf(ANCHOR_CLOSE, start);
  if (end === -1) return '';
  return previous.slice(start, end + ANCHOR_CLOSE.length).replace(/\n+$/, '');
}

const anchorPart = anchorMd
  ? `\n${ANCHOR_OPEN} (${stamp}) ===\n${anchorMd}\n${ANCHOR_CLOSE}`
  : (anchorFromSnapshot() ? `\n${anchorFromSnapshot()}` : '');

const out = `=== Craft: «Общение с Владом», живой инжект (${stamp}) ===\n${md}\n=== конец правил общения — действуют в этой сессии ===${anchorPart}`;

if (process.env.BEHAVIOR_RULES_TEST_SNAPSHOT || channelReady()) {
  // Атомарная перезапись: соседняя сессия читает целую версию, не половину.
  try {
    fs.mkdirSync(path.dirname(snapshot), { recursive: true });
  } catch { /* каталог уже есть или не создаётся — решит запись ниже */ }
  try {
    fs.writeFileSync(`${snapshot}.tmp`, `${out}\n`);
    fs.renameSync(`${snapshot}.tmp`, snapshot);
    process.stdout.write(`Правила общения с Владом обновлены из Craft (${fs.statSync(snapshot).size} байт, ${stamp}) — полный текст в контексте через импорт снимка, обрезки нет.\n`);
    process.exit(0);
  } catch {
    try {
      fs.rmSync(`${snapshot}.tmp`, { force: true });
    } catch { /* временного файла и не было */ }
    // Снимок не записался — печатаем тело, иначе правила пропадут молча.
  }
}

// Потолок вывода считается в БАЙТАХ, как считал его шелл в этой локали: срез
// посреди многобайтного символа обязан дать те же байты.
let body = Buffer.from(`${out}\n`, 'utf8');
if (body.length - 1 > BUDGET) {
  body = Buffer.concat([
    body.subarray(0, BUDGET),
    Buffer.from(`\n…[обрезано бюджетом инжекта — канал импорта не установлен, поставь его прогоном install.sh; дочитай источник живьём: Craft MCP blocks get ${communicationId} --depth -1]\n`, 'utf8'),
  ]);
}
process.stdout.write(body);
