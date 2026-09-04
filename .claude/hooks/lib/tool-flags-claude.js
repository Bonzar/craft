// Имена инструментов харнеса Claude Code: адаптер. Здесь и только здесь слой
// знает, что «Write» — это правка, «Bash» — команда, а «ExitPlanMode» — показ
// плана. Общая часть получает от него ПРИЗНАКИ и форму вызова данными.
//
// toolFlags(инструмент, вход) → признаки записи журнала:
//   edit — правка содержимого; note_write — запись в базу заметок (какой
//   инструмент ею является, знает адаптер базы); plan — показ плана;
//   question — вопрос Владу; stage — стадия хода, повтор которой внутри хода
//   является сигналом; push — отправка сделанного; skill — имя скилла;
//   incident_skill — этот скилл разбирает инцидент.
// callShape(инструмент, вход) → чем был вызов: {kind: 'edit', path},
//   {kind: 'command', text} либо {} — по нему считается «менял ли мир».
// toolScope(инструмент, вход) → область вызова: {reads: true} — только читает,
//   {session: true} — правит ход самой сессии, {} — всё остальное. По ней общая
//   часть решает, трогает ли вызов мир.
// semanticInput(инструмент, вход) → вход без СЛУЖЕБНЫХ полей этого харнеса. По
//   нему общая часть считает хеш вызова и не знает, какие поля входа служебные.
import { looksLikePush } from './write-targets-git.js';
// Рабочая система и скиллы проекта — не дело адаптера харнеса: он берёт от них
// готовую возможность и готовый список.
import { isNoteWrite, noteRef } from './note-write-craft.js';
import { INCIDENT_SKILLS } from './incident-skills.js';

// Путь входа у правящих инструментов. Один список: их было два, и они
// разъезжались.
const EDIT_TOOL_PATH = {
  Write: 'file_path', Edit: 'file_path', MultiEdit: 'file_path', NotebookEdit: 'notebook_path',
};

// Стадии хода, повтор которых внутри одного хода — сигнал: показ плана и вопрос.
const STAGE_TOOLS = new Set(['ExitPlanMode', 'AskUserQuestion']);

export function toolFlags(tool, input = {}) {
  const flags = {};
  if (EDIT_TOOL_PATH[tool] || isNoteWrite(tool)) flags.edit = true;
  if (isNoteWrite(tool)) flags.note_write = true;
  if (tool === 'ExitPlanMode') flags.plan = true;
  if (tool === 'AskUserQuestion') flags.question = true;
  if (STAGE_TOOLS.has(tool)) flags.stage = true;
  if (tool === 'Bash' && looksLikePush(input.command)) flags.push = true;
  if (tool === 'Skill' && typeof input.skill === 'string') {
    flags.skill = input.skill;
    if (INCIDENT_SKILLS.has(input.skill)) flags.incident_skill = true;
  }
  return flags;
}

export function callShape(tool, input = {}) {
  const editPath = EDIT_TOOL_PATH[tool];
  if (editPath) return { kind: 'edit', path: input[editPath] || '' };
  if (isNoteWrite(tool)) return { kind: 'edit', path: noteRef(input) || tool };
  if (tool === 'Bash') return { kind: 'command', text: String(input.command || '') };
  return {};
}

// Гейт стоит на правках МИРА: файлы, командная строка, база, внешние сервисы.
// Всё, что мир не трогает, — не его дело. Отсюда два основания пройти, и у
// каждого своё.
//
// Первое: инструмент только ЧИТАЕТ — менять ему нечего.
const READING_TOOLS = new Set([
  'Read', 'Grep', 'Glob', 'LS', 'WebFetch', 'WebSearch', 'ToolSearch', 'BashOutput',
  'TaskList', 'TaskGet', 'TaskOutput', 'ListAgents', 'ListSkills', 'ListPlugins',
  'ListMcpResourcesTool', 'ReadMcpResourceTool', 'ReadNotifications',
]);

// Второе: инструмент правит ход САМОЙ СЕССИИ, а не мир. План, вопрос Владу,
// список работы, расписание пробуждения — это состояние разговора: реестр про
// них ничего не знает и знать не должен, а сверка спрашивала бы гейт про самого
// себя. Тот же принцип уже записан для файлов: служебное состояние харнесса
// эфемерно, и тудушки названы там прямым текстом.
const SESSION_TOOLS = new Set([
  'TaskCreate', 'TaskUpdate', 'TaskStop', 'ExitPlanMode', 'EnterPlanMode',
  'AskUserQuestion', 'Skill', 'ScheduleWakeup', 'SendMessage', 'SendUserFile',
  'ReportFindings', 'SuggestSkills', 'ShowOnboardingRolePicker',
]);

const READING_VERBS = 'get|list|read|search|fetch|show|describe|resolve|status|view|find|count|check';

// Имя MCP-инструмента говорит само за себя, когда в нём стоит глагол чтения.
// Это не догадка о поведении, а признак: сервер, который пишет, называет
// операцию иначе.
function mcpReads(name) {
  const op = String(name).replace(/^mcp__.*?__/, '');
  // Глагол стоит либо в начале имени (list_repos), либо на конце после
  // подчёркивания (craft_read).
  return new RegExp(`^(${READING_VERBS})(_|$)`, 'i').test(op)
    || new RegExp(`_(${READING_VERBS})$`, 'i').test(op);
}

// Подагенты: их запуск сам по себе мир не трогает — трогает то, что делает
// подагент, и решает это имя его роли.
const SUBAGENT_TOOLS = new Set(['Task', 'Agent', 'Workflow']);

// Читающие подагенты названы поимённо: разведка и критика мира не трогают, а
// гейт на их запуске стоил бы вызова модели на каждом плане.
const READING_AGENTS = new Set([
  'Explore', 'Plan', 'plan-critic', 'plan-critic-unit', 'plan-critic-seams',
  'plan-critic-verdict', 'comment-analyzer', 'type-design-analyzer',
  'silent-failure-hunter', 'typescript-reviewer', 'react-reviewer',
  'pr-test-analyzer', 'claude-code-guide',
]);

// Обслуживание СОБСТВЕННОГО хода: подписаться на события своего PR, разбудить
// себя проверкой через час, снять подписку, переименовать сессию. Мир от этого
// не меняется — меняется то, когда и на что агент проснётся, и реестр про такие
// вещи ничего не знает. Без этого правила гейт запирал агента ровно там, где он
// обязан довести работу до зелёного: подписку и отложенную проверку не
// пропускал, и красный PR оставался без присмотра.
const SESSION_OPS = /(subscribe_pr_activity|send_later|_wakeup|set_session_(title|tags))$/;

// Область вызова для общей части: только чтение, обслуживание собственного хода
// или всё остальное. Имена инструментов кончаются ЗДЕСЬ.
export function toolScope(tool, input = {}) {
  if (READING_TOOLS.has(tool)) return { reads: true };
  if (SESSION_TOOLS.has(tool) || SESSION_OPS.test(tool)) return { session: true };
  if (SUBAGENT_TOOLS.has(tool)) {
    return READING_AGENTS.has(String(input.subagent_type || '')) ? { reads: true } : {};
  }
  if (/^mcp__/.test(tool)) return mcpReads(tool) ? { reads: true } : {};
  return {};
}

// Служебные поля входа: смысла вызова они не несут, а меняются от повтора к
// повтору. Описание вызова модель переписывает своими словами, таймаут ставит
// по настроению, номер запуска, который надо прибить, у каждого запуска свой —
// с ними «тот же вызов» после отказа переставал узнаваться, и ложный отказ не
// засчитывался. Список по инструментам, а не общий: `description` у одного
// инструмента — ярлык вызова, у другого может быть смыслом. Чтение фонового
// вывода (`BashOutput`) сюда не входит: его `bash_id` говорит, ЧЕЙ вывод читать,
// то есть это смысл вызова, а не служебное поле.
const VOLATILE_INPUT = new Map([
  ['Bash', ['description', 'timeout', 'run_in_background']],
  ['KillShell', ['shell_id']],
  ['Task', ['description']],
]);

export function semanticInput(tool, input = {}) {
  const src = input && typeof input === 'object' ? input : {};
  const drop = VOLATILE_INPUT.get(tool);
  if (!drop) return src;
  const out = {};
  for (const key of Object.keys(src)) if (!drop.includes(key)) out[key] = src[key];
  return out;
}
