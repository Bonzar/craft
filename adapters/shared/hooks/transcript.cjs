'use strict';

const fs = require('node:fs');

function lastAssistantText(file) {
  let text = '';
  try { text = fs.readFileSync(file, 'utf8'); } catch { return ''; }
  const lines = text.split('\n');
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (!lines[i].trim()) continue;
    let entry;
    try { entry = JSON.parse(lines[i]); } catch { continue; }
    const message = entry && entry.message;
    if (!message || message.role !== 'assistant') continue;
    const content = message.content;
    if (typeof content === 'string' && content.trim()) return content.trim();
    if (!Array.isArray(content)) continue;
    const said = content.filter((item) => item && item.type === 'text' && typeof item.text === 'string')
      .map((item) => item.text).join('\n').trim();
    if (said) return said;
  }
  return '';
}

function entries(file) {
  let text = '';
  try { text = fs.readFileSync(file, 'utf8'); } catch { return []; }
  return text.split('\n').filter(Boolean).map((line) => {
    try { return JSON.parse(line); } catch { return {}; }
  });
}

function textBlocks(message) {
  const content = message && message.content;
  if (typeof content === 'string') return content.trim() ? [content] : [];
  if (!Array.isArray(content)) return [];
  return content
    .filter((item) => item && item.type === 'text' && typeof item.text === 'string')
    .map((item) => item.text);
}

function isTurnStart(entry) {
  if (entry.type === 'last-prompt') return true;
  if (entry.type !== 'user' || entry.isSidechain) return false;
  const content = entry.message && entry.message.content;
  if (typeof content === 'string') return true;
  if (!Array.isArray(content)) return false;
  return content.some((item) => item && item.type === 'text')
    && content.every((item) => !item || item.type !== 'tool_result');
}

// All user-visible assistant text produced in the current turn. This differs
// from lastAssistantText: a report emitted before a later tool call is still
// visible to the user and must remain subject to Stop policy.
function visibleTurnText(file) {
  const parsed = entries(file);
  let start = 0;
  parsed.forEach((entry, index) => {
    if (isTurnStart(entry)) start = index + 1;
  });
  const chunks = [];
  for (const entry of parsed.slice(start)) {
    const role = entry.type || (entry.message && entry.message.role);
    if (role !== 'assistant' || entry.isSidechain) continue;
    chunks.push(...textBlocks(entry.message));
  }
  return chunks.join('\n').replace(/\n+$/, '');
}

// Native transcript parsing stays in the adapter. The callback converts a
// harness tool name to the canonical route, so this helper does not know any
// provider vocabulary.
function editedFiles(file, routeOf) {
  const found = new Set();
  for (const entry of entries(file)) {
    const content = entry && entry.message && entry.message.content;
    if (!Array.isArray(content)) continue;
    for (const item of content) {
      if (!item || item.type !== 'tool_use' || routeOf(item.name) !== 'file.mutate') continue;
      const input = item.input && typeof item.input === 'object' ? item.input : {};
      const target = input.file_path || input.notebook_path || '';
      if (target) found.add(target);
    }
  }
  return [...found].sort();
}

module.exports = { lastAssistantText, visibleTurnText, editedFiles };
