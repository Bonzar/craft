import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
let cached;

export function systemLayout() {
  if (cached) return cached;
  const file = process.env.CRAFT_SYSTEM_LAYOUT || path.join(repo, 'adapters', 'system-layout.json');
  try { cached = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { cached = { homeZones: [] }; }
  return cached;
}

export function homeZoneState(file, home) {
  for (const zone of systemLayout().homeZones || []) {
    const prefix = path.join(home, zone.root) + path.sep;
    if (!file.startsWith(prefix)) continue;
    const relative = file.slice(prefix.length);
    const first = relative.split(path.sep)[0];
    const gated = (zone.gatedDirectories || []).includes(first)
      || (zone.gatedFiles || []).includes(relative);
    return { matched: true, gated };
  }
  return { matched: false, gated: false };
}

export function isSystemPath(file) {
  const normalized = String(file).replaceAll('\\\\', '/');
  return (systemLayout().homeZones || []).some((zone) =>
    normalized === zone.root || normalized.startsWith(`${zone.root}/`) || normalized.includes(`/${zone.root}/`));
}

export function layoutPattern(name) {
  const value = systemLayout()[name];
  return value ? new RegExp(value, 'i') : /$a/;
}
