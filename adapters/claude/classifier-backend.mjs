import { spawnSync } from 'node:child_process';
import {
  classifierFailure, inferClassifierReason, normalizeClassifierFailure,
} from '../../core/classifier/failure.mjs';

function processFailure(result) {
  let detail = result.error?.message || result.stderr || '';
  if (!detail && result.stdout) {
    try {
      const value = JSON.parse(result.stdout);
      const errors = Array.isArray(value.errors) ? value.errors.join(' ') : '';
      detail = [value.subtype, value.result, errors].filter(Boolean).join(': ');
    } catch {
      detail = result.stdout;
    }
  }
  detail ||= 'classifier process failed';
  let reason = result.error?.code === 'ETIMEDOUT' ? 'timeout' : inferClassifierReason(detail);
  if (/error_max_turns|structured output (?:was )?not produced/i.test(detail)) reason = 'invalid_output';
  return classifierFailure(reason, { detail });
}

export function runClassifier({ model, prompt, systemPrompt, schema, turns, tools, timeoutMs, env }) {
  const { $schema: _dialect, ...nativeSchema } = schema;
  const nativeTools = String(tools || '').split(',').filter(Boolean).map((tool) => ({
    'file.read': 'Read',
    'text.search': 'Grep',
    'file.list': 'Glob',
  })[tool] || '').filter(Boolean).join(',');
  const args = [
    '-p', '--setting-sources', 'project', '--model', model,
    '--max-turns', String(Math.max(2, Number(turns) || 0)), '--system-prompt', systemPrompt,
    '--json-schema', JSON.stringify(nativeSchema), '--output-format', 'json',
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
  if (result.status !== 0 || result.error) throw processFailure(result);
  try {
    const outer = JSON.parse(result.stdout);
    const output = outer.structured_output ?? JSON.parse(outer.result);
    if (!output || typeof output !== 'object' || Array.isArray(output)
      || Object.keys(output).length !== 1 || typeof output.output !== 'string' || !output.output) {
      throw classifierFailure('invalid_output', { detail: 'classifier output violates schema' });
    }
    return output;
  } catch (error) {
    const failure = normalizeClassifierFailure(error);
    throw classifierFailure(
      failure.reason === 'unknown' ? 'invalid_output' : failure.reason,
      failure,
    );
  }
}
