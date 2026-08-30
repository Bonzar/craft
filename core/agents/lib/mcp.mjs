import { invokeAgent } from '../../workflows/lib/agent-runtime.mjs';

const INVOKE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    agentId: { type: 'string', minLength: 1 },
    task: { type: 'string', minLength: 1 },
    context: {},
  },
  required: ['agentId', 'task'],
};

const tool = {
  name: 'invoke',
  description: 'Invoke any registered canonical child agent through the shared core runtime.',
  inputSchema: INVOKE_SCHEMA,
};

function result(id, value) { return { jsonrpc: '2.0', id, result: value }; }
function error(id, code, message) { return { jsonrpc: '2.0', id, error: { code, message } }; }

export async function handleAgentRpc(message, invoke = invokeAgent) {
  const id = message?.id ?? null;
  if (message?.method === 'initialize') {
    return result(id, {
      protocolVersion: message.params?.protocolVersion || '2025-03-26',
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: 'craft-agent-core', version: '1.0.0' },
    });
  }
  if (message?.method === 'notifications/initialized') return null;
  if (message?.method === 'tools/list') return result(id, { tools: [tool] });
  if (message?.method !== 'tools/call') return error(id, -32601, 'method not found');
  if (message.params?.name !== 'invoke') return error(id, -32602, 'unknown tool');
  const args = message.params?.arguments;
  const keys = Object.keys(args || {});
  if (!args || typeof args !== 'object' || Array.isArray(args)
    || keys.some((key) => !['agentId', 'task', 'context'].includes(key))
    || typeof args.agentId !== 'string' || !args.agentId
    || typeof args.task !== 'string' || !args.task.trim()) {
    return error(id, -32602, 'invalid invoke arguments');
  }
  try {
    const value = await invoke({ agentId: args.agentId, task: args.task, ...(keys.includes('context') ? { context: args.context } : {}) });
    return result(id, {
      content: [{ type: 'text', text: JSON.stringify(value) }],
      structuredContent: value,
      isError: false,
    });
  } catch (caught) {
    const messageText = String(caught?.message || caught);
    return result(id, {
      content: [{ type: 'text', text: messageText }],
      isError: true,
    });
  }
}
