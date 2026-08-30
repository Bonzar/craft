#!/usr/bin/env node
import fs from 'node:fs';

function strings(value) {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function validTask(task) {
  if (!task || typeof task !== 'object' || Array.isArray(task)) return false;
  if (!Object.keys(task).every((key) => ['title', 'where', 'anchor'].includes(key))) return false;
  return typeof task.title === 'string' && strings(task.where) && typeof task.anchor === 'string';
}

function validAdd(add) {
  if (!add || typeof add !== 'object' || Array.isArray(add)) return false;
  if (!Object.keys(add).every((key) => ['kind', 'goal', 'goal_new', 'tasks'].includes(key))) return false;
  if (add.kind !== undefined && !['work', 'ban'].includes(add.kind)) return false;
  if (add.goal !== undefined && typeof add.goal !== 'string') return false;
  if (add.goal_new !== undefined && typeof add.goal_new !== 'string') return false;
  if (add.kind === 'ban') {
    return typeof add.goal_new === 'string' && add.goal_new.trim().length > 0
      && (add.tasks === undefined || (Array.isArray(add.tasks) && add.tasks.length === 0));
  }
  return Array.isArray(add.tasks) && add.tasks.every(validTask);
}

try {
  const value = JSON.parse(fs.readFileSync(0, 'utf8'));
  const keys = value && typeof value === 'object' && !Array.isArray(value) ? Object.keys(value) : [];
  const valid = keys.length > 0 && keys.every((key) => ['add', 'close', 'lift'].includes(key))
    && Array.isArray(value.add) && value.add.every(validAdd)
    && (value.close === undefined || strings(value.close))
    && (value.lift === undefined || strings(value.lift));
  if (!valid) process.exit(1);
  process.stdout.write(`${JSON.stringify(value)}\n`);
} catch { process.exit(1); }
