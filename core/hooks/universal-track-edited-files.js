#!/usr/bin/env node
// PostToolUse ledger of files changed in this session. Transcript shapes differ
// across harnesses, so the core records edits from the universal event instead.
// The ledger gives Stop quality hooks one client-neutral input.
import fs from 'node:fs';
import path from 'node:path';
import { readEvent } from './lib/event.js';
import { editedFilesState } from './lib/paths.js';
import { canonicalPatchChanges } from '../contracts/action.mjs';

const { route, input, cwd } = readEvent();
let files = [];
if (route === 'file.mutate' && typeof input.target === 'string') {
  files = [input.target];
} else if (route === 'file.patch') {
  files = (canonicalPatchChanges(input) || []).flatMap((change) => [change.file, change.destination].filter(Boolean));
}
if (!files.length) process.exit(0);

const root = cwd || process.env.PWD || process.cwd();
const normalized = files.map((file) => (path.isAbsolute(file) ? file : path.resolve(root, file)));
const state = editedFilesState();
let previous = [];
try { previous = fs.readFileSync(state, 'utf8').split('\n').filter(Boolean); } catch { /* first write */ }
try {
  fs.writeFileSync(state, `${[...new Set([...previous, ...normalized])].sort().join('\n')}\n`);
} catch { /* quality checks fall back to the transcript */ }
