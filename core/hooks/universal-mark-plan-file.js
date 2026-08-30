#!/usr/bin/env node
// PostToolUse на запись файла плана: запоминает путь плана ЭТОЙ сессии, по
// которому судят гейт критика (universal-guard-plan-critic) и его метка
// (universal-mark-plan-critic). Каталог планов общий на все сессии и проекты,
// поэтому «самый свежий файл» там — ненадёжный признак: параллельная сессия
// подсунет чужой план.
//
// Планы дочерних агентов не запоминаются: гейт судит о плане, который
// показывают Владу. Роль артефакта определяет adapter; core не угадывает её по
// нативному имени файла.
//
// Fail quiet: сломанная запоминалка не должна клинить работу.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { planFileMarker } from './lib/paths.js';

const { raw, event, route, input } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

const artifact = input.planArtifact;
if (!artifact || artifact.kind !== 'plan' || artifact.role !== 'primary' || typeof artifact.path !== 'string') process.exit(0);
const fp = artifact.path;

try {
  fs.writeFileSync(planFileMarker(), fp);
} catch { /* не записалось — гейт критика просто не найдёт файл плана */ }
