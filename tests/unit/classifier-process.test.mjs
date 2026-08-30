import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { classify } from '../../core/hooks/lib/classifier.js';

test('classifier stdout from a failed process is never accepted', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-failed-process.'));
  const script = path.join(dir, 'classifier.sh');
  fs.writeFileSync(script, '#!/usr/bin/env bash\nprintf \'COVERED:Ц1.1:stale partial verdict\\n\'\nexit 1\n');
  try {
    assert.equal(classify(script, 'cover', [], 'change'), 'UNAVAILABLE');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
