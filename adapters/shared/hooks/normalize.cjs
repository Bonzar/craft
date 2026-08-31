'use strict';

const path = require('node:path');
const { lastAssistantText, visibleTurnText, editedFiles } = require('./transcript.cjs');
const { parsePatchChanges } = require('./patch.cjs');

// Harness wire fields stop here. Core receives a canonical envelope plus an
// opaque operation identifier for semantic classification when the route does
// not prove an effect.

const READ_NAMES = new Set([
  'Read', 'Grep', 'Glob', 'LS', 'WebFetch', 'WebSearch', 'ToolSearch', 'BashOutput',
  'TaskList', 'TaskGet', 'TaskOutput', 'ListAgents', 'ListSkills', 'ListPlugins',
  'ListMcpResourcesTool', 'ReadMcpResourceTool', 'ReadNotifications',
  'get_goal', 'list_agents', 'list_mcp_resources', 'list_mcp_resource_templates',
  'read_mcp_resource', 'view_image', 'read_thread', 'list_threads',
]);

const SESSION_ROUTES = new Map([
  ['TaskCreate', 'session.work'], ['TaskUpdate', 'session.work'], ['TaskStop', 'session.work'],
  ['create_goal', 'session.work'], ['update_goal', 'session.work'],
  ['request_permissions', 'session.permission'],
  ['EnterPlanMode', 'session.plan'], ['update_plan', 'session.plan'],
  ['Skill', 'session.skill'], ['SuggestSkills', 'session.skill'],
  ['ScheduleWakeup', 'session.schedule'],
  ['SendMessage', 'agent.control'], ['send_message', 'agent.control'],
  ['followup_task', 'agent.control'], ['wait_agent', 'agent.control'], ['interrupt_agent', 'agent.control'],
  ['SendUserFile', 'session.delivery'], ['ReportFindings', 'session.delivery'],
  ['ShowOnboardingRolePicker', 'session.ui'],
]);

const CRAFT_READ_OPERATIONS = new Set(['craft_read', 'craft_mcp_craft_read']);
const CRAFT_WRITE_OPERATIONS = new Set(['craft_write', 'craft_mcp_craft_write']);
const READ_OPERATIONS = new Set([...CRAFT_READ_OPERATIONS, 'list_mcp_resources', 'list_mcp_resource_templates', 'read_mcp_resource']);
const SCHEDULE_OPS = /(subscribe_pr_activity|send_later|_wakeup)$/;
const UI_OPS = /set_session_(title|tags)$/;

function nativeName(event) {
  if (typeof event.tool_name === 'string') return event.tool_name;
  const name = event.hook_event_name;
  return name === 'SubagentStart' || name === 'SubagentStop' ? 'Agent' : '';
}

function nativeInput(event) {
  if (event.tool_input && typeof event.tool_input === 'object') return event.tool_input;
  if (typeof event.agent_type === 'string') return { subagent_type: event.agent_type };
  return {};
}

function mcpOperation(name) {
  return String(name).replace(/^mcp__.*?__/, '');
}

function routeFor(name) {
  const operation = /^mcp__/.test(name) ? mcpOperation(name) : '';
  if (name === 'ExitPlanMode') return 'plan.submit';
  if (name === 'AskUserQuestion' || name === 'request_user_input') return 'session.question';
  if (['Task', 'Agent', 'Workflow', 'spawn_agent', 'create_thread'].includes(name)) return 'agent.invoke';
  if (['Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(name)) return 'file.mutate';
  if (name === 'apply_patch') return 'file.patch';
  if (name === 'Bash' || name === 'exec_command' || name === 'write_stdin') return 'command.run';
  if (CRAFT_WRITE_OPERATIONS.has(operation)) return 'data.mutate';
  if (CRAFT_READ_OPERATIONS.has(operation)) return 'read';
  if (READ_NAMES.has(name)) return 'read';
  if (SESSION_ROUTES.has(name)) return SESSION_ROUTES.get(name);
  if (SCHEDULE_OPS.test(name)) return 'session.schedule';
  if (UI_OPS.test(name)) return 'session.ui';
  if (READ_OPERATIONS.has(operation)) return 'read';
  return name ? 'unknown' : 'none';
}

function eventRouteFor(name) {
  return ({
    SessionStart: 'session.start',
    UserPromptSubmit: 'user.prompt',
    SubagentStart: 'agent.start',
    SubagentStop: 'agent.stop',
    PreToolUse: 'action.before',
    PostToolUse: 'action.after',
    PostToolUseFailure: 'action.failure',
    Stop: 'turn.stop',
    PreCompact: 'context.compact',
  })[name] || 'unknown';
}

function tag(text, name) {
  const match = new RegExp(`<${name}>([^<]*)</${name}>`).exec(String(text || ''));
  return match ? match[1] : '';
}

function notificationOf(event) {
  const prompt = String(event.prompt || event.user_prompt || '');
  if (!prompt.includes('<task-notification>')) return null;
  return {
    invocationId: tag(prompt, 'task-id'),
    status: tag(prompt, 'status'),
    result: tag(prompt, 'result'),
  };
}

function receiptOf(response) {
  if (response && typeof response === 'object' && !Array.isArray(response)) {
    return response.agentId || response.taskId || response.task_id || response.runId || response.run_id || '';
  }
  const text = typeof response === 'string' ? response : JSON.stringify(response || '');
  const match = /agentId:\s*([A-Za-z0-9_-]+)|[Tt]ask[-_ ]?[Ii][Dd][^A-Za-z0-9_-]{1,3}([A-Za-z0-9_-]{4,})|(wf_[a-z0-9-]{6,})/.exec(text);
  return match ? (match[1] || match[2] || match[3] || '') : '';
}

function planArtifact(paths, runtime, options) {
  if (runtime !== 'claude' || typeof options.planRoot !== 'string' || !options.planRoot) return undefined;
  const root = path.resolve(options.planRoot);
  const plans = paths.filter((file) => {
    if (typeof file !== 'string' || path.extname(file) !== '.md') return false;
    const resolved = path.resolve(file);
    const relative = path.relative(root, resolved);
    return relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative);
  });
  if (!plans.length) return undefined;
  const artifactPath = plans.find((file) => !file.includes('-agent-')) || plans[0];
  return { kind: 'plan', role: artifactPath.includes('-agent-') ? 'child' : 'primary', path: artifactPath };
}

function payloadFor(route, input, sourceName, runtime, options) {
  const raw = input && typeof input === 'object' ? input : {};
  if (route === 'file.mutate') {
    const edits = Array.isArray(raw.edits)
      ? raw.edits.map((edit) => ({
        previousText: edit && typeof edit.old_string === 'string' ? edit.old_string : '',
        newText: edit && typeof edit.new_string === 'string' ? edit.new_string : '',
      }))
      : [];
    const payload = {
      target: raw.file_path || raw.notebook_path || '',
      previousText: raw.old_string || '',
      newText: raw.new_string ?? raw.content ?? raw.new_source ?? '',
      wholeDocument: typeof raw.content === 'string',
      edits,
    };
    const artifact = planArtifact([payload.target], runtime, options);
    return artifact ? { ...payload, planArtifact: artifact } : payload;
  }
  if (route === 'file.patch') {
    const changes = parsePatchChanges(raw.command || raw.patch || '');
    const artifact = planArtifact(changes.flatMap((change) => [change.file, change.destination].filter(Boolean)), runtime, options);
    return artifact ? { changes, planArtifact: artifact } : { changes };
  }
  if (route === 'command.run') {
    return {
      command: raw.command || '',
      streamInput: raw.chars || '',
      unbounded: !raw.command,
    };
  }
  if (route === 'data.mutate') return { command: raw.command || '' };
  if (route === 'agent.invoke') {
    let agentId = raw.subagent_type || raw.agent_type || raw.agent || '';
    if (!agentId && sourceName === 'Workflow') {
      const source = `${raw.scriptPath || ''}\n${raw.script || ''}`;
      if (source.includes('plan-critic-fan')) agentId = 'workflow-fan';
    }
    return {
      agentId,
      prompt: raw.prompt || raw.message || '',
    };
  }
  if (route === 'plan.submit') return { plan: raw.plan || '' };
  if (route === 'session.question') return { questions: raw.questions || [], raw };
  if (route === 'read') {
    return {
      target: raw.file_path || raw.path || '',
      query: raw.pattern || raw.query || '',
      raw,
    };
  }
  if (route === 'unknown') return { raw };
  return { raw };
}

function normalizeHarnessEvent(event, runtime, originalIntent, options = {}) {
  const source = event && typeof event === 'object' ? event : {};
  const notification = notificationOf(source);
  const name = nativeName(source);
  const input = nativeInput(source);
  const route = notification ? 'agent.invoke' : routeFor(name);
  const response = source.tool_response === undefined && source.hook_event_name === 'SubagentStop'
    ? source.last_assistant_message
    : source.tool_response;
  const transcript = source.transcript_path || '';
  const assistantText = source.last_assistant_message || lastAssistantText(transcript);
  const assistantTurnText = source.assistant_turn_text || visibleTurnText(transcript) || assistantText;
  const changedFiles = Array.isArray(source.session_edited_files)
    ? source.session_edited_files.filter((file) => typeof file === 'string' && file)
    : editedFiles(transcript, routeFor);
  const failed = Boolean(response && typeof response === 'object' && !Array.isArray(response)
    && (response.is_error === true || response.isError === true || response.error));
  const canonicalEventName = source.hook_event_name === 'PostToolUse' && failed
    ? 'action.failure'
    : eventRouteFor(source.hook_event_name || '');
  const payload = notification
    ? { agentId: '', prompt: '', ...notification }
    : payloadFor(route, input, name, runtime, options);
  if (route === 'agent.invoke' && !notification) {
    payload.invocationId = source.agent_id || source.task_id || receiptOf(response);
    payload.status = source.status || (source.hook_event_name === 'SubagentStop' ? 'completed' : '');
    payload.result = source.hook_event_name === 'SubagentStop' ? response : '';
  }
  return {
    schemaVersion: 1,
    event: {
      name: notification ? 'agent.stop' : canonicalEventName,
      cwd: source.cwd || '',
      mode: source.permission_mode || '',
      prompt: source.prompt || source.user_prompt || '',
      transcript,
      sessionId: source.session_id || '',
      stopActive: source.stop_hook_active === true,
      lastAssistantMessage: assistantText,
      previousAssistantText: assistantText,
      assistantTurnText,
      sessionEditedFiles: changedFiles,
      originalIntent: originalIntent || source.original_intent || '',
      invocationId: source.tool_use_id || source.tool_call_id || source.call_id || '',
    },
    action: {
      route,
      operation: name,
      payload,
    },
    response,
    outcome: {
      status: failed ? 'error' : 'success',
      error: failed ? String(response.error || response.content || '') : '',
      result: response,
    },
    runtime: runtime || '',
  };
}

module.exports = { normalizeHarnessEvent, routeFor, eventRouteFor };
