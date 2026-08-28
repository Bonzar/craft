// Что делает вызов: пишет он в мир или нет. ЕДИНСТВЕННОЕ определение записи на
// весь контур — им пользуется ядро гейта и все его правила.
//
// Зачем отдельно. Раньше определение жило в двух гвардах разными словами: гейт
// смотрел на цели записи, гвард якоря требовал от команды доказать, что она
// только читает. Одна и та же команда у одного была записью, у другого нет —
// и увидеть это можно было лишь на живом прогоне, подсунув хуку событие.
//
// ПОЛИТИКА: инструмент ищет ПРИЗНАКИ ЗАПИСИ, а не доказательства чтения. Команда,
// про которую ни одного признака не видно, не пишет. Отсюда и берётся то, ради
// чего всё затевалось: незнакомый клиент (codex, свежая утилита) проходит, пока
// не пишет, а не отвергается за то, что про него ничего не известно.
//
// Непокрыто и названо честно: неопознанная конструкция записи; пакетные
// менеджеры и сетевые операции (пишут своей логикой, не перенаправлением);
// перенаправление в закавыченную цель — кавычки вычёркиваются, чтобы «больше» в
// сравнении не считалось записью.
import {
  isEphemeral, gitEphemeral, bashWriteTargets, cleanTarget, treeMutations,
} from './write-targets.js';

// Гейт стоит на правках МИРА: файлы, командная строка, база, внешние сервисы.
// Всё, что мир не трогает, — не его дело. Отсюда два основания пройти.
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
// себя.
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
// не меняется — меняется то, когда и на что агент проснётся.
const SESSION_OPS = /(subscribe_pr_activity|send_later|_wakeup|set_session_(title|tags))$/;

const FILE_EDIT_TOOLS = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit'];
const SUBAGENT_TOOLS = ['Task', 'Agent', 'Workflow'];

// Craft-запись опознаётся по СУФФИКСУ имени, а не по полному: префикс MCP-сервера
// Craft меняется на переподключении (mcp__Craft__… в одной сессии, mcp__‹uuid›__…
// в следующей) — точное сравнение молча перестало бы гейтить на первой ротации.
export const isCraftWrite = (tool) => /__craft_write$/.test(String(tool));

function ephemeral(target) {
  return isEphemeral(target) || gitEphemeral(target);
}

function answer(fields) {
  return {
    writes: false, ephemeralOnly: false, kind: 'none', why: '', targets: [], realTargets: [], ...fields,
  };
}

// writeIntent({ tool, input }) → { writes, ephemeralOnly, kind, why, targets, realTargets }
//   writes        — вызов меняет мир, и решать про него правилам гейта;
//   ephemeralOnly — цели есть, но все временные: правка мира не переживёт сессию;
//   kind          — поверхность: file | command | craft | tool | agent | none;
//   targets       — все распознанные цели, realTargets — из них невременные.
export function writeIntent({ tool, input = {} }) {
  const name = String(tool || '');

  if (isCraftWrite(name)) {
    return answer({ writes: true, kind: 'craft', why: 'запись в базу' });
  }

  if (FILE_EDIT_TOOLS.includes(name)) {
    const fp = input.file_path || input.notebook_path || '';
    if (!fp) return answer({ kind: 'file', why: 'файл не назван' });
    if (ephemeral(fp)) {
      return answer({
        kind: 'file', ephemeralOnly: true, why: `правка эфемерного файла (${fp})`, targets: [fp],
      });
    }
    return answer({
      writes: true, kind: 'file', why: `правка файла (${fp})`, targets: [fp], realTargets: [fp],
    });
  }

  if (name === 'Bash') return commandIntent(input.command || '');

  if (READING_TOOLS.has(name) || SESSION_TOOLS.has(name) || SESSION_OPS.test(name)) {
    return answer({ why: 'вызов мира не трогает' });
  }

  if (SUBAGENT_TOOLS.includes(name)) {
    const type = String(input.subagent_type || '');
    if (READING_AGENTS.has(type)) return answer({ kind: 'agent', why: `читающий подагент (${type})` });
    return answer({ writes: true, kind: 'agent', why: `подагент, который может править (${type || 'без имени'})` });
  }

  if (/^mcp__/.test(name)) {
    if (mcpReads(name)) return answer({ kind: 'tool', why: 'в имени операции стоит глагол чтения' });
    return answer({ writes: true, kind: 'tool', why: `инструмент ${name}` });
  }

  // Всё прочее: то, чего сегодня ещё нет. Молчать про него нельзя — иначе новый
  // инструмент открывает мир в обход гейта самим фактом своей новизны.
  return answer({ writes: true, kind: 'tool', why: `инструмент ${name}` });
}

// Команда: цели записи собираются из двух источников — разбора записи
// (перенаправление, tee, правка на месте, копирование, запись из интерпретатора)
// и списка команд, которые меняют дерево сами.
//
// РЕШЕНИЕ ПРИНИМАЕТСЯ ПО ЦЕЛЯМ, А НЕ ПО ИМЕНИ: команда, у которой все цели
// временные, проходит — иначе уборка своего же мусора (`rm /tmp/f`) упиралась бы
// в гейт.
function commandIntent(cmd) {
  if (!cmd.trim()) return answer({ kind: 'command', why: 'команда пуста' });

  const mutations = treeMutations(cmd);
  const targets = [...bashWriteTargets(cmd), ...mutations.targets]
    .map(cleanTarget)
    .filter(Boolean);
  const realTargets = targets.filter((t) => !ephemeral(t));

  if (realTargets.length) {
    return answer({
      writes: true, kind: 'command', targets, realTargets, why: `команда пишет в ${realTargets.join(', ')}`,
    });
  }

  // Мутатор без путей в аргументах (`git clean`, `git reset --hard`): пропускать
  // его нечем, эфемерность проверять не на чем.
  if (mutations.unscoped.length) {
    return answer({
      writes: true, kind: 'command', targets, why: `команда «${mutations.unscoped[0]}» меняет рабочее дерево`,
    });
  }

  if (targets.length) {
    return answer({
      kind: 'command', ephemeralOnly: true, targets, why: 'все цели записи временные',
    });
  }

  return answer({ kind: 'command', why: 'признаков записи в команде не видно' });
}
