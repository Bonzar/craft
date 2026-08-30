import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

function updatedText(text) {
  const lines = text.split('\n');
  const starts = lines.flatMap((line, index) => line.trim() === '[features]' ? [index] : []);
  if (starts.length > 1) throw new Error('features section is ambiguous');

  if (!starts.length) {
    const base = text.replace(/\s*$/, '');
    return `${base}${base ? '\n\n' : ''}[features]\ncode_mode_host = false\n`;
  }

  const start = starts[0];
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^\s*\[/.test(lines[index])) { end = index; break; }
  }

  const body = lines.slice(start + 1, end);
  const keys = body.flatMap((line, index) => /^\s*code_mode_host\s*=/.test(line) ? [index] : []);
  if (keys.length > 1) throw new Error('code_mode_host setting is ambiguous');
  if (keys.length === 1) body[keys[0]] = 'code_mode_host = false';
  else {
    let insertion = body.length;
    while (insertion > 0 && body[insertion - 1] === '') insertion -= 1;
    body.splice(insertion, 0, 'code_mode_host = false');
  }
  return [...lines.slice(0, start + 1), ...body, ...lines.slice(end)].join('\n');
}

export function ensureDirectToolRouting(configPath) {
  const text = fs.existsSync(configPath) ? fs.readFileSync(configPath, 'utf8') : '';
  const next = updatedText(text);
  if (next === text) return false;
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  const mode = fs.existsSync(configPath) ? fs.statSync(configPath).mode : 0o600;
  const temporary = path.join(path.dirname(configPath), `.${path.basename(configPath)}.craft-routing.${process.pid}`);
  try {
    fs.writeFileSync(temporary, next, { mode });
    fs.renameSync(temporary, configPath);
  } finally {
    try { fs.rmSync(temporary, { force: true }); } catch { /* already renamed */ }
  }
  return true;
}

async function cli() {
  const [mode, configPath] = process.argv.slice(2);
  if (mode !== 'ensure' || !configPath) throw new Error('usage: tool-routing-config.mjs ensure CONFIG_PATH');
  ensureDirectToolRouting(configPath);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  cli().catch((error) => {
    process.stderr.write(`${String(error?.message || error)}\n`);
    process.exitCode = 1;
  });
}
