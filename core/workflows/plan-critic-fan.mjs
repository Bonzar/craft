#!/usr/bin/env node
import fs from 'node:fs';
import { invokeAgent, mapAgents } from './lib/agent-runtime.mjs';

function input() {
  const value = JSON.parse(fs.readFileSync(0, 'utf8'));
  const allowed = new Set(['plan', 'units', 'dossier', 'findings', 'backend', 'sessionId', 'cwd']);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('workflow input must be an object');
  for (const key of Object.keys(value)) if (!allowed.has(key)) throw new Error(`unknown workflow field: ${key}`);
  if (typeof value.plan !== 'string' || !value.plan) throw new Error('plan path is required');
  if (!Array.isArray(value.units) || !value.units.length || value.units.some((unit) => typeof unit !== 'string')) throw new Error('units are required');
  return value;
}

function task(role, value, unit = '') {
  const ctx = value.dossier ? ` Read the factual dossier at ${value.dossier}.` : '';
  const repeat = value.findings ? ` This is a repeat pass; read prior findings at ${value.findings} and only verify their closure, regressions, and new critical issues.` : '';
  if (role === 'plan-critic-seams') return `Review only cross-unit seams in ${value.plan}: source coverage, contradictions, duplicates, ordering, and form consistency.${ctx}${repeat}`;
  return `Review only unit “${unit}” in ${value.plan}; other units and seams are assigned elsewhere.${ctx}${repeat}`;
}

async function main() {
  const value = input();
  const jobs = [...value.units.map((unit) => ({ agentId: 'plan-critic-unit', unit })), { agentId: 'plan-critic-seams' }];
  const runs = await mapAgents(jobs, (job) => invokeAgent({
    agentId: job.agentId, task: task(job.agentId, value, job.unit), backend: value.backend,
    sessionId: value.sessionId, cwd: value.cwd || process.cwd(), context: { plan: value.plan, unit: job.unit || null },
  }));
  const reports = [];
  const failures = [];
  runs.forEach((run, index) => {
    if (run.ok) reports.push({ critic: jobs[index].unit || 'seams', report: run.value.result });
    else failures.push({ critic: jobs[index].unit || 'seams', error: run.error });
  });
  if (!reports.length) throw new Error('all plan critics failed');
  const verdict = await invokeAgent({
    agentId: 'plan-critic-verdict', backend: value.backend, sessionId: value.sessionId,
    cwd: value.cwd || process.cwd(),
    task: 'Synthesize the supplied reports for one plan. Preserve the required rubric and finish with exactly one machine-readable verdict line.',
    context: { reports, failedCritics: failures },
  });
  let result = verdict.result.trim();
  const last = result.split('\n').filter(Boolean).at(-1);
  if (!['Вердикт: блокеров нет', 'Вердикт: есть блокеры'].includes(last)) throw new Error('verdict agent returned an invalid final line');
  if (failures.length && last === 'Вердикт: блокеров нет') result = `${result.slice(0, -last.length).trimEnd()}\n\nНе все критики завершились успешно: ${failures.map((item) => item.critic).join(', ')}.\n\nВердикт: есть блокеры`;
  return { schemaVersion: 1, status: 'ok', workflowId: 'plan-critic-fan', result, failedCritics: failures };
}

try { process.stdout.write(`${JSON.stringify(await main())}\n`); }
catch (error) {
  process.stdout.write(`${JSON.stringify({ schemaVersion: 1, status: 'error', workflowId: 'plan-critic-fan', result: 'Вердикт: есть блокеры', error: String(error?.message || error) })}\n`);
  process.exitCode = 1;
}
