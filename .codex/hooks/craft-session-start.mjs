#!/usr/bin/env node
// Thin Codex SessionStart adapter. Policy and snapshot mechanics live in core.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCraftContext } from '../../adapters/codex/hooks/lib/context.js';

function readEvent() {
  try {
    const text = fs.readFileSync(0, 'utf8');
    return text.trim() ? JSON.parse(text) : {};
  } catch {
    return {};
  }
}

function claim(event) {
  const safe = (value, fallback) => String(value || fallback).replace(/[^a-zA-Z0-9_.-]/g, '_');
  const lock = path.join(os.tmpdir(), `craft-codex-start.${safe(event.session_id, 'unknown')}.${safe(event.source, 'startup')}.lock`);
  try {
    const fd = fs.openSync(lock, 'wx');
    fs.closeSync(fd);
    return true;
  } catch {
    try {
      if (Date.now() - fs.statSync(lock).mtimeMs < 30_000) return false;
      fs.rmSync(lock, { force: true });
      fs.closeSync(fs.openSync(lock, 'wx'));
      return true;
    } catch {
      return false;
    }
  }
}

export const buildContext = buildCraftContext;

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const event = readEvent();
  if (claim(event)) process.stdout.write(`${buildCraftContext(event)}\n`);
}
