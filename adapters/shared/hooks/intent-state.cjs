'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

function fileFor(runtime, sessionId) {
  const key = crypto.createHash('sha256').update(`${runtime}\n${sessionId}`).digest('hex');
  return path.join(os.tmpdir(), `craft-turn-intent.${key}`);
}

function originalIntent(event, runtime) {
  const sessionId = String(event.session_id || event.thread_id || event.conversation_id || 'default');
  const file = fileFor(runtime, sessionId);
  const submitted = event.hook_event_name === 'UserPromptSubmit'
    ? String(event.prompt || event.user_prompt || '')
    : '';
  if (submitted.trim()) {
    try { fs.writeFileSync(file, submitted, { mode: 0o600 }); } catch { /* missing intent makes transition block */ }
    return submitted;
  }
  try { return fs.readFileSync(file, 'utf8'); } catch { return ''; }
}

module.exports = { originalIntent };
