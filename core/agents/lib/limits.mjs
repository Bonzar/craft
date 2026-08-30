import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

function alive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

export function acquire(sessionId, maxConcurrency) {
  const safe = String(sessionId || 'default').replace(/[^a-zA-Z0-9_.-]/g, '_');
  const dir = path.join(os.tmpdir(), `craft-agent-runtime.${safe}`);
  fs.mkdirSync(dir, { recursive: true });
  for (let slot = 0; slot < maxConcurrency; slot += 1) {
    const lock = path.join(dir, `slot-${slot}`);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const fd = fs.openSync(lock, 'wx', 0o600);
        fs.writeFileSync(fd, String(process.pid));
        fs.closeSync(fd);
        return () => { try { fs.rmSync(lock, { force: true }); } catch { /* stale cleanup handles it */ } };
      } catch (error) {
        if (error?.code !== 'EEXIST') throw error;
        const owner = Number(fs.readFileSync(lock, 'utf8'));
        if (!owner || !alive(owner)) {
          fs.rmSync(lock, { force: true });
          continue;
        }
        break;
      }
    }
  }
  throw new Error('agent concurrency limit reached');
}

export function childPermission(parent, requested) {
  if (parent !== 'read-only' && parent !== 'workspace-write') return 'read-only';
  return parent === 'read-only' ? 'read-only' : requested;
}
