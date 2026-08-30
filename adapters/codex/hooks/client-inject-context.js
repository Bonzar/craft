#!/usr/bin/env node
// Codex transport for the live Craft context. Fetch/cache mechanics are shared
// with the existing injectors; only delivery differs: stdout becomes one
// additionalContext field in dispatch.js instead of a CLAUDE.md @-import.
import { readEvent } from '../../../core/hooks/lib/event.js';
import { hookOnce } from '../../../core/hooks/lib/once.js';
import { buildCraftContext } from './lib/context.js';

const { raw, event } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
process.stdout.write(`${buildCraftContext(event)}\n`);
