#!/usr/bin/env node
// PreCompact-хук: перед компакцией контекста записывает след сессии в
// ~/.claude/session-data/precompact-<session_id>.md — timestamp, transcript_path
// и cwd, чтобы resume-session мог найти контекст после потери. Портирован из
// ECC (pre-compact.js) в минимальном виде: без LLM-резюме, только якорь.
//
// Тихий и fail open: любая ошибка — молчаливый exit 0, компакцию не задерживаем.
import fs from 'node:fs';
import path from 'node:path';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';

const { raw, event, cwd } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

// Имя файла собирается из идентификатора сессии, поэтому всё, что не буква,
// цифра, подчёркивание или дефис, заменяется подчёркиванием: чужой разделитель
// пути увёл бы запись из каталога.
const sid = (event.session_id || 'unknown').replace(/[^a-zA-Z0-9_-]/g, '_');
const transcript = event.transcript_path || '';

const dir = path.join(process.env.HOME || '', '.claude', 'session-data');
try {
  fs.mkdirSync(dir, { recursive: true });
} catch {
  process.exit(0);
}

const stamp = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
const body = `# PreCompact snapshot\n\n- timestamp: ${stamp}\n- transcript_path: ${transcript || '—'}\n- cwd: ${cwd || process.env.PWD || process.cwd()}\n`;
try {
  fs.writeFileSync(path.join(dir, `precompact-${sid}.md`), body);
} catch { /* след не записался — компакцию всё равно не держим */ }
