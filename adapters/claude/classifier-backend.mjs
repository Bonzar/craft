import { spawnSync } from 'node:child_process';

export function runClassifier({ model, prompt, systemPrompt, schema, turns, tools, timeoutMs, env }) {
  const nativeTools = String(tools || '').split(',').filter(Boolean).map((tool) => ({
    'file.read': 'Read',
    'text.search': 'Grep',
    'file.list': 'Glob',
  })[tool] || '').filter(Boolean).join(',');
  const args = [
    '-p', '--model', model, '--max-turns', String(turns), '--system-prompt', systemPrompt,
    '--json-schema', JSON.stringify(schema), '--output-format', 'json',
    '--exclude-dynamic-system-prompt-sections', '--no-session-persistence',
  ];
  if (nativeTools) args.push('--allowedTools', nativeTools);
  const childEnv = { ...env };
  delete childEnv.CLAUDE_CODE_SESSION_ID;
  delete childEnv.CLAUDE_CODE_CHILD_SESSION;
  delete childEnv.CLAUDE_PID;
  delete childEnv.CLAUDE_CODE_REMOTE_SESSION_ID;
  const result = spawnSync(env.CRAFT_CLAUDE_CMD || 'claude', args, {
    cwd: '/tmp', env: childEnv, input: prompt, encoding: 'utf8', timeout: timeoutMs, maxBuffer: 32 * 1024 * 1024,
  });
  if (result.status !== 0 || result.error) throw new Error(result.stderr || result.error?.message || 'Claude classifier failed');
  const outer = JSON.parse(result.stdout);
  return outer.structured_output ?? JSON.parse(outer.result);
}
