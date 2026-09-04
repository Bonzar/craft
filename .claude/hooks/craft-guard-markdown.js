#!/usr/bin/env node
// PreToolUse(craft_write) guard: запись и правка в Craft идут только структурной
// формой — блок-объектом, где текст лежит полем. Голый текстовый флаг при записи
// не используется никогда: он нормализует отступ к нулю и не несёт структурных
// полей, а правка перепарсивает текст.
//
// Структурная форма разрешена, даже когда внутри её содержимого встречается
// текст самого текстового флага — там он часть записываемого текста, а не флаг
// команды. Блокируется только текстовый флаг БЕЗ структурного.
//
// Инструмент опознаётся по концу имени, а не по полному: префикс MCP-сервера
// Craft меняется при переподключении, и точное совпадение молча перестало бы
// гейтить в момент смены идентификатора.
// Структурной формы мало: она обязана НЕСТИ структурные поля, а не полагаться на
// текст. Разделитель — тот случай, где умолчание молчит: без lineStyle Craft
// ставит regular, каким бы ни был шаблон, и подмена видна только в json.
import { readEvent } from './lib/event-claude.js';
import { deny } from './lib/decide-claude.js';

const LINE_STYLES = new Set(['strong', 'regular', 'light', 'extraLight', 'pageBreak']);

const NO_STYLE = 'Заблокировано правилом Craft: у блока-разделителя не задан lineStyle. '
  + 'Стиль из markdown при записи не выводится — без явного поля разделитель станет '
  + 'обычным, каким бы ни был шаблон. Возьми стиль из шаблона свежим чтением в json '
  + 'и передай полем.';

// Аргумент после --json бывает в одинарных кавычках, в двойных и голым, а самих
// --json в команде бывает много: батч через «;» — обычная форма записи.
function payloads(cmd) {
  const re = /--json[\s=]+('([^']*)'|"((?:[^"\\]|\\.)*)"|(\S+))/g;
  const out = [];
  let m;
  while ((m = re.exec(cmd)) !== null) out.push(m[2] ?? m[3] ?? m[4] ?? '');
  return out;
}

// Блоки приходят и одиночным объектом, и массивом, и детьми внутри — обходим вглубь.
function hasBareLine(node) {
  if (Array.isArray(node)) return node.some(hasBareLine);
  if (!node || typeof node !== 'object') return false;
  if (node.type === 'line' && !LINE_STYLES.has(node.lineStyle)) return true;
  return Object.values(node).some(hasBareLine);
}

// Payload не прочитался как JSON — судим по его тексту. Грубо, зато не пропускает
// молча: неразобранная команда упирается в отказ, а не в умолчание Craft.
function looksLikeBareLine(text) {
  return /"type"\s*:\s*"line"/.test(text)
    && !new RegExp(`"lineStyle"\\s*:\\s*"(${[...LINE_STYLES].join('|')})"`).test(text);
}

const { tool, input } = readEvent();
if (!/__craft_write$/.test(tool)) process.exit(0);

const command = input.command || '';

// Структурная форма идёт дальше — на разбор полей, а не мимо проверки.
if (/(^|\s)--json(\s|=)/m.test(command)) {
  for (const payload of payloads(command)) {
    let parsed = null;
    try {
      parsed = JSON.parse(payload);
    } catch { /* не прочиталось — ниже судим по тексту */ }
    if (parsed === null ? looksLikeBareLine(payload) : hasBareLine(parsed)) deny(NO_STYLE);
  }
  process.exit(0);
}

if (/(^|\s)--markdown(\s|=|$)/m.test(command)) {
  deny('Заблокировано правилом Craft: запись и правка идут только через --json (структурный блок-объект, текст в поле markdown). Голый флаг --markdown при записи не используется никогда — он нормализует отступ к 0 и не несёт структурных полей, а update перепарсивает текст. Пересобери команду через --json (см. «MCP-механики Craft»).');
}
process.exit(0);
