#!/usr/bin/env node
// PostToolUse на запись файла плана: запоминает путь плана ЭТОЙ сессии, по
// которому судят гейт критика (universal-guard-plan-critic) и его метка
// (universal-mark-plan-critic). Каталог планов общий на все сессии и проекты,
// поэтому «самый свежий файл» там — ненадёжный признак: параллельная сессия
// подсунет чужой план.
//
// Планы подагентов (в имени «-agent-») не запоминаются: гейт судит о плане,
// который показывают Владу, а не о черновиках подагентов.
//
// Fail quiet: сломанная запоминалка не должна клинить работу.
import fs from 'node:fs';
import { readEvent } from './lib/event-claude.js';
import { hookOnce } from './lib/once.js';
import { planFileMarker } from './lib/paths.js';

const { raw, core, input } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);

const fp = input.file_path || '';
if (!/\/plans\/.*\.md$/.test(fp)) process.exit(0);
if (fp.includes('-agent-')) process.exit(0);

try {
  fs.writeFileSync(planFileMarker(), fp);
} catch { /* не записалось — гейт критика просто не найдёт файл плана */ }
