// Имена инструментов харнеса Claude Code: адаптер. Здесь и только здесь слой
// знает, что «Write» — это правка, «Bash» — команда, а «ExitPlanMode» — показ
// плана. Общая часть получает от него ПРИЗНАКИ и форму вызова данными.
//
// toolFlags(инструмент, вход) → признаки записи журнала:
//   edit — правка содержимого; craft_write — запись в базу Craft; plan — показ
//   плана; question — вопрос Владу; stage — стадия хода, повтор которой внутри
//   хода является сигналом; push — отправка сделанного; skill — имя скилла.
// callShape(инструмент, вход) → чем был вызов: {kind: 'edit', path},
//   {kind: 'command', text} либо {} — по нему считается «менял ли мир».
import { looksLikePush } from './write-targets-git.js';

// Путь входа у правящих инструментов. Один список: их было два, и они
// разъезжались.
const EDIT_TOOL_PATH = {
  Write: 'file_path', Edit: 'file_path', MultiEdit: 'file_path', NotebookEdit: 'notebook_path',
};

const isCraftWrite = (tool) => /__craft_write$/.test(String(tool || ''));

// Стадии хода, повтор которых внутри одного хода — сигнал: показ плана и вопрос.
const STAGE_TOOLS = new Set(['ExitPlanMode', 'AskUserQuestion']);

export function toolFlags(tool, input = {}) {
  const flags = {};
  if (EDIT_TOOL_PATH[tool] || isCraftWrite(tool)) flags.edit = true;
  if (isCraftWrite(tool)) flags.craft_write = true;
  if (tool === 'ExitPlanMode') flags.plan = true;
  if (tool === 'AskUserQuestion') flags.question = true;
  if (STAGE_TOOLS.has(tool)) flags.stage = true;
  if (tool === 'Bash' && looksLikePush(input.command)) flags.push = true;
  if (tool === 'Skill' && typeof input.skill === 'string') flags.skill = input.skill;
  return flags;
}

export function callShape(tool, input = {}) {
  const editPath = EDIT_TOOL_PATH[tool];
  if (editPath) return { kind: 'edit', path: input[editPath] || '' };
  if (isCraftWrite(tool)) return { kind: 'edit', path: String(input.block_id || input.id || tool) };
  if (tool === 'Bash') return { kind: 'command', text: String(input.command || '') };
  return {};
}
