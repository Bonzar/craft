import assert from 'node:assert/strict';
import test from 'node:test';
import patch from '../../adapters/shared/hooks/patch.cjs';

const { parsePatchChanges } = patch;

test('apply_patch parser returns every file and its before/after material', () => {
  const changes = parsePatchChanges(`*** Begin Patch
*** Update File: src/a.js
@@
-old
+new
*** Add File: src/b.js
+created
*** Delete File: src/c.js
-removed
*** End Patch`);
  assert.deepEqual(changes, [
    { kind: 'update', file: 'src/a.js', oldText: 'old', newText: 'new' },
    { kind: 'add', file: 'src/b.js', oldText: '', newText: 'created' },
    { kind: 'delete', file: 'src/c.js', oldText: 'removed', newText: '' },
  ]);
});

test('apply_patch parser treats Move to as a second mutation target', () => {
  const changes = parsePatchChanges(`*** Begin Patch
*** Update File: /tmp/staged.md
*** Move to: README.md
@@
-old
+new
*** End Patch`);
  assert.deepEqual(changes, [
    {
      kind: 'update',
      file: '/tmp/staged.md',
      destination: 'README.md',
      oldText: 'old',
      newText: 'new',
    },
  ]);
});
