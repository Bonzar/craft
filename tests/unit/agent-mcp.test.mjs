import assert from 'node:assert/strict';
import test from 'node:test';
import { handleAgentRpc } from '../../core/agents/lib/mcp.mjs';

test('universal agent MCP exposes the canonical invoke contract', async () => {
  const listed = await handleAgentRpc({ jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} });
  assert.equal(listed.result.tools.length, 1);
  assert.equal(listed.result.tools[0].name, 'invoke');
  assert.deepEqual(listed.result.tools[0].inputSchema.required, ['agentId', 'task']);

  const calls = [];
  const response = await handleAgentRpc({
    jsonrpc: '2.0', id: 2, method: 'tools/call',
    params: { name: 'invoke', arguments: { agentId: 'comment-analyzer', task: 'review', context: { file: 'a.js' } } },
  }, async (request) => {
    calls.push(request);
    return { schemaVersion: 1, status: 'ok', agentId: request.agentId, backend: 'test', model: 'test', depth: 1, result: 'done' };
  });
  assert.deepEqual(calls, [{ agentId: 'comment-analyzer', task: 'review', context: { file: 'a.js' } }]);
  assert.equal(response.result.structuredContent.result, 'done');
  assert.equal(response.result.isError, false);
});
