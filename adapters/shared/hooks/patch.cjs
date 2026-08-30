'use strict';

// Native apply-patch text belongs to the harness boundary. Core receives only
// structured mutations and never needs to know a provider tool grammar.
function parsePatchChanges(command) {
  const lines = String(command || '').split('\n');
  const changes = [];
  let current = null;

  const push = () => {
    if (!current) return;
    current.oldText = current.old.join('\n');
    current.newText = current.fresh.join('\n');
    delete current.old;
    delete current.fresh;
    changes.push(current);
    current = null;
  };

  for (const line of lines) {
    const header = /^\*\*\* (Add|Update|Delete) File: (.+)$/.exec(line);
    if (header) {
      push();
      current = { kind: header[1].toLowerCase(), file: header[2].trim(), old: [], fresh: [] };
      continue;
    }
    const move = /^\*\*\* Move to: (.+)$/.exec(line);
    if (current && move) {
      current.destination = move[1].trim();
      continue;
    }
    if (!current) continue;
    if (line.startsWith('@@')) continue;
    if (line.startsWith('+') && !line.startsWith('+++')) current.fresh.push(line.slice(1));
    else if (line.startsWith('-') && !line.startsWith('---')) current.old.push(line.slice(1));
    else if (line.startsWith(' ')) {
      current.old.push(line.slice(1));
      current.fresh.push(line.slice(1));
    }
  }
  push();
  return changes.filter((change) => change.file);
}

module.exports = { parsePatchChanges };
