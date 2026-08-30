import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { capability } from '../runtime/capabilities.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

function validate(request) {
  const keys = Object.keys(request || {}).sort();
  const allowed = ['intent', 'sessionRef'];
  if (keys.some((key) => !allowed.includes(key))) throw new Error('unknown plan transition field');
  if (!request || typeof request.sessionRef !== 'string' || !request.sessionRef) throw new Error('sessionRef is required');
  if (typeof request.intent !== 'string' || !request.intent.trim()) throw new Error('intent is required');
  return request;
}

export async function planTransition(runtime, request) {
  const root = path.join(repo, 'adapters', runtime);
  const config = JSON.parse(fs.readFileSync(path.join(root, 'config.json'), 'utf8'));
  const support = capability(config, 'native-plan-transition');
  if (support.status !== 'supported') return { ...support, blocked: true };
  const backend = await import(pathToFileURL(path.join(root, 'plan-mode-backend.mjs')).href);
  if (typeof backend.createPlanTurn !== 'function') throw new Error('adapter has no planning transition backend');
  return backend.createPlanTurn(validate(request), config);
}
