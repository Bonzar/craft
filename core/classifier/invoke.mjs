#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseCoverVerdict, parsePreflightVerdict } from './verdict.mjs';
import { normalizeIngest } from './validate-ingest.mjs';
import {
  classifierFailure, isTransientClassifierFailure, normalizeClassifierFailure,
} from './failure.mjs';

const root = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(root, '..', '..');
const schemaFile = path.join(root, 'schemas', 'backend-output.schema.json');
const schema = JSON.parse(fs.readFileSync(schemaFile, 'utf8'));

function validEnvelope(value) {
  return value && Object.keys(value).length === 1 && typeof value.output === 'string' && value.output.length > 0;
}

function validDecision(output, mode) {
  if (mode === 'preflight') return parsePreflightVerdict(output) ? output : '';
  if (mode === 'cover') {
    const parsed = parseCoverVerdict(output);
    if (parsed && parsed.kind !== 'UNAVAILABLE') return output;
    const forms = [
      [/^РАЗРЕШЕНО ПОВЕРХ (.+)$/, 'OVERRIDE:$1'],
      [/^ЗАПРЕЩЕНО (.+)$/, 'FORBIDDEN:$1'],
      [/^НЕ ПОКРЫТА: (.+)$/, 'UNCOVERED:$1'],
      [/^ПОКРЫТА (.+)$/, 'COVERED:$1'],
      [/^ЧЕРНОВОЕ$/, 'DRAFT'],
    ];
    for (const [pattern, replacement] of forms) if (pattern.test(output)) return output.replace(pattern, replacement);
    return '';
  }
  if (mode === 'delta') {
    if (/^(?:REPEATSALL|REPEATS:.+|CLEAN)$/.test(output)) return output;
    if (output === 'ПОВТОРЯЕТ ВСЁ') return 'REPEATSALL';
    if (output === 'ДЕЛЬТА ЧИСТАЯ') return 'CLEAN';
    const repeats = /^ПОВТОРЫ: (.+)$/.exec(output);
    return repeats ? `REPEATS:${repeats[1]}` : '';
  }
  if (mode === 'ingest') return normalizeIngest(output.replace(/^\s*```[^\n]*\n?|\n?```\s*$/g, '')) || '';
  return output;
}

function exactCandidate(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (Object.keys(value).sort().join(',') !== 'backend,model') return null;
  if (!/^[a-z0-9][a-z0-9-]*$/.test(value.backend)) return null;
  if (typeof value.model !== 'string' || !value.model.trim()) return null;
  return { backend: value.backend, model: value.model.trim() };
}

function emitNotice(value) {
  process.stderr.write(`CRAFT_CLASSIFIER_NOTICE ${JSON.stringify(value)}\n`);
}

function unavailable(value) {
  emitNotice(value);
  process.stdout.write('UNAVAILABLE\n');
}

function wait(ms) {
  return ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve();
}

async function main() {
  const backend = process.env.CRAFT_CLASSIFIER_BACKEND || process.env.CRAFT_RUNTIME || '';
  if (!/^[a-z0-9][a-z0-9-]*$/.test(backend)) return unavailable({ type: 'classifier_configuration_invalid' });
  const adapterRoot = process.env.CRAFT_ADAPTER_ROOT || path.join(repo, 'adapters');
  const ownDir = process.env.CRAFT_ADAPTER_DIR || path.join(adapterRoot, backend);
  let selected;
  try {
    selected = JSON.parse(fs.readFileSync(path.join(ownDir, 'config.json'), 'utf8')).classifier;
  } catch {
    return unavailable({ type: 'classifier_configuration_invalid' });
  }
  let candidates;
  if (Array.isArray(selected?.candidates)) {
    candidates = selected.candidates.map(exactCandidate);
    if (!candidates.length || candidates.some((value) => !value)) return unavailable({ type: 'classifier_configuration_invalid' });
  } else if (typeof selected?.model === 'string' && selected.model) {
    const legacy = Array.isArray(selected.fallbackModels) ? selected.fallbackModels : [];
    candidates = [selected.model, ...legacy].map((model) => exactCandidate({ backend, model }));
  } else {
    return unavailable({ type: 'classifier_configuration_invalid' });
  }
  const envFallbacks = (process.env.CRAFT_CLASSIFIER_FALLBACK_MODELS || '').split(',').map((item) => item.trim()).filter(Boolean);
  if (process.env.CRAFT_CLASSIFIER_MODEL) candidates[0] = { ...candidates[0], model: process.env.CRAFT_CLASSIFIER_MODEL };
  candidates.push(...envFallbacks.map((model) => ({ backend: candidates[0].backend, model })));
  candidates = [...new Map(candidates.map((value) => [`${value.backend}\0${value.model}`, value])).values()];
  const prompt = fs.readFileSync(0, 'utf8');
  const common = { prompt, systemPrompt: process.env.CRAFT_CLASSIFIER_SYSTEM_PROMPT || '', schema, schemaFile, turns: Number(process.env.CRAFT_CLASSIFIER_TURNS || 1), tools: process.env.CRAFT_CLASSIFIER_TOOLS || '', timeoutMs: Number(process.env.PLAN_CLASSIFIER_TIMEOUT || 1800) * 1000, env: process.env };
  const mode = process.env.CRAFT_CLASSIFIER_MODE || '';
  const deadline = Date.now() + common.timeoutMs;
  const retryDelay = Math.max(0, Math.min(5_000, Number(process.env.CRAFT_CLASSIFIER_RETRY_DELAY_MS || 250) || 0));
  const failed = new Map();
  for (let index = 0; index < candidates.length; index += 1) {
    const candidate = candidates[index];
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const remaining = deadline - Date.now();
        if (remaining <= 0) throw classifierFailure('timeout', { detail: 'classifier deadline expired' });
        const dir = candidate.backend === backend && process.env.CRAFT_ADAPTER_DIR
          ? ownDir : path.join(adapterRoot, candidate.backend);
        const module = await import(pathToFileURL(path.join(dir, 'classifier-backend.mjs')).href);
        if (typeof module.runClassifier !== 'function') {
          throw classifierFailure('unsupported', { detail: 'classifier backend has no run contract' });
        }
        const value = await module.runClassifier({ ...common, model: candidate.model, timeoutMs: remaining });
        const output = validEnvelope(value) ? validDecision(value.output, mode) : '';
        if (!output) throw classifierFailure('invalid_output', { detail: 'classifier decision violates mode schema' });
        if (index > 0) emitNotice({ type: 'classifier_fallback_selected', ...candidate });
        process.stdout.write(`${output}\n`);
        return;
      } catch (error) {
        const failure = normalizeClassifierFailure(error);
        const notice = { type: 'classifier_attempt_failed', ...candidate, ...failure };
        failed.set(`${candidate.backend}\0${candidate.model}`, { ...candidate, ...failure });
        emitNotice(notice);
        if (attempt === 0 && isTransientClassifierFailure(failure) && Date.now() + retryDelay < deadline) {
          await wait(retryDelay);
          continue;
        }
        break;
      }
    }
  }
  unavailable({ type: 'classifier_unavailable', attempts: [...failed.values()] });
}

await main();
