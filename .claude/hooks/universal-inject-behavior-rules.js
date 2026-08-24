#!/usr/bin/env node
// SessionStart hook (устанавливается в пользовательский слой): живой инжект
// правил общения с Владом из Craft в сессии ВНЕ craft-репо — код-сессии, чужие
// проекты. Канон правил остаётся в Craft (правится с телефона, действует сразу
// во всех сессиях); никакого коммитнутого кэша — только живое чтение на старте.
//
// В craft-репо не работает: там роутер (включая «Общение с Владом») инжектится
// целиком своим хуком плюс импортом в CLAUDE.md.
//
// ДОСТАВКА — ФАЙЛОМ, НЕ ПЕЧАТЬЮ. Вывод SessionStart-хука обрезается на 10 000
// символах, а страница правил общения давно длиннее: печатью терялась её
// последняя треть, причём молча. Поэтому тело уходит в файл-снимок, а его
// подтягивает `@`-импорт в пользовательском CLAUDE.md — у импортов потолка нет
// (проверено живой пробой: вложенный вызов из чужого каталога видит
// импортированный текст). В выводе остаётся только строка-отчёт.
//
// Снимок ОБЩИЙ для всех сессий, поэтому он никогда не пустеет и не сносится:
// перезапись идёт атомарно, уже готовым текстом, и соседняя сессия читает либо
// прежнюю версию, либо новую, но не пустоту. Свежесть показывает метка времени
// внутри снимка, а не его снос: при мёртвой сети прежний текст остаётся, но
// датирован — агент видит, что читает вчерашнее.
//
// Канал не установлен (в личном CLAUDE.md нет строки импорта) — хук печатает
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
// Инжектор роутера ищется в любой из двух версий: пока слой переезжает, рядом
// лежат обе.
function isCraftRepo(dir) {
  if (!dir) return false;
  return ['craft-inject-router.js', 'craft-inject-router.sh']
    .some((name) => fs.existsSync(path.join(dir, '.claude', 'hooks', name)));
}
if (isCraftRepo(process.env.CLAUDE_PROJECT_DIR)) process.exit(0);

loadEnv();

const communicationId = process.env.CRAFT_COMMUNICATION_ID || '7485dec3-f1c2-4f17-e88a-72994f772b84';
const claudeMd = process.env.CRAFT_USER_CLAUDE_MD || path.join(os.homedir(), '.claude', 'CLAUDE.md');
const snapshot = process.env.CRAFT_BEHAVIOR_SNAPSHOT
  || path.join(os.homedir(), '.claude', 'craft-live', 'behavior-rules.md');
// Печать тела остаётся аварийным путём, и её потолок прежний: вывод капится.
const BUDGET = 9500;

function fallback(why) {
  process.stdout.write(`⚠️ Правила общения с Владом не загружены из Craft (${why}). Они обязательны в любой сессии: при доступном Craft MCP прочитай блок ${communicationId} (blocks get --depth -1) перед содержательными ответами; без Craft — держи минимум: структура вместо полотна, выбор — кнопками, факт отдельно от догадки, ссылки кликабельными.\n`);
  process.exit(0);
}

// Канал жив, только пока импорт снимка стоит в личном CLAUDE.md: без него файл
// никто не прочитает, и тело обязано идти печатью.
function channelReady() {
  try {
    return fs.readFileSync(claudeMd, 'utf8').includes(snapshot);
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

const out = `=== Craft: «Общение с Владом», живой инжект (${stamp}) ===\n${md}\n=== конец правил общения — действуют в этой сессии ===`;

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
