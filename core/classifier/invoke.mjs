#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(root, '..', '..');
const schemaFile = path.join(root, 'schemas', 'backend-output.schema.json');
const schema = JSON.parse(fs.readFileSync(schemaFile, 'utf8'));

function valid(value) {
  return value && Object.keys(value).length === 1 && typeof value.output === 'string' && value.output.length > 0;
}

async function main() {
  const backend = process.env.CRAFT_CLASSIFIER_BACKEND || process.env.CRAFT_RUNTIME || '';
  if (!/^[a-z0-9][a-z0-9-]*$/.test(backend)) throw new Error('classifier backend is required');
  const dir = process.env.CRAFT_ADAPTER_DIR || path.join(repo, 'adapters', backend);
  const config = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));
  const module = await import(pathToFileURL(path.join(dir, 'classifier-backend.mjs')).href);
  if (typeof module.runClassifier !== 'function') throw new Error(`classifier backend has no runClassifier: ${backend}`);
  const selected = config.classifier;
  if (!selected) throw new Error(`classifier backend is not configured: ${backend}`);
  const envFallbacks = (process.env.CRAFT_CLASSIFIER_FALLBACK_MODELS || '').split(',').map((item) => item.trim()).filter(Boolean);
  const configuredFallbacks = Array.isArray(selected.fallbackModels) ? selected.fallbackModels : [];
  const models = [...new Set([process.env.CRAFT_CLASSIFIER_MODEL || selected.model, ...configuredFallbacks, ...envFallbacks])];
  const prompt = fs.readFileSync(0, 'utf8');
  const common = { prompt, systemPrompt: process.env.CRAFT_CLASSIFIER_SYSTEM_PROMPT || '', schema, schemaFile, turns: Number(process.env.CRAFT_CLASSIFIER_TURNS || 1), tools: process.env.CRAFT_CLASSIFIER_TOOLS || '', timeoutMs: Number(process.env.PLAN_CLASSIFIER_TIMEOUT || 1800) * 1000, env: process.env };
  for (const model of models) {
    try {
      const value = module.runClassifier({ ...common, model });
      if (valid(value)) { process.stdout.write(`${value.output}\n`); return; }
    } catch { /* only an explicitly configured next model may be attempted */ }
  }
  process.stdout.write('UNAVAILABLE\n');
}

await main();
