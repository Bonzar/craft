import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const definitions = path.join(root, 'definitions');

function frontmatter(text) {
  const match = /^---\n([\s\S]*?)\n---\n([\s\S]*)$/.exec(text);
  if (!match) throw new Error('definition has no frontmatter');
  const meta = {};
  for (const line of match[1].split('\n')) {
    const at = line.indexOf(':');
    if (at > 0) meta[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return { meta, prompt: match[2].trim() };
}

export function loadConfig() {
  return JSON.parse(fs.readFileSync(path.join(root, 'config.json'), 'utf8'));
}

export function listAgents() {
  return fs.readdirSync(definitions).filter((name) => name.endsWith('.md')).sort()
    .map((name) => loadAgent(name.slice(0, -3)));
}

export function loadAgent(id) {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(id)) throw new Error(`invalid agent id: ${id}`);
  const file = path.join(definitions, `${id}.md`);
  const { meta, prompt } = frontmatter(fs.readFileSync(file, 'utf8'));
  if (meta.id !== id) throw new Error(`agent id mismatch in ${file}`);
  if (!meta.description || !meta.model_profile || !meta.permission) throw new Error(`incomplete agent metadata: ${id}`);
  return { id, file, prompt, ...meta };
}
