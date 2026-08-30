import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { requiresPlanGate, writeIntent } from '../../core/plan-gate/write-intent.js';

const command = (value) => ({ route: 'command.run', payload: { command: value, unbounded: false } });
const root = fileURLToPath(new URL('../..', import.meta.url));

test('unknown command is unproven and reaches plan-gate', () => {
  const intent = writeIntent(command('future-deployer --ship'));
  assert.equal(intent.effect, 'unknown');
  assert.equal(requiresPlanGate(intent), true);
});

test('broken command syntax is fail-closed', () => {
  const intent = writeIntent(command('cat "unterminated'));
  assert.equal(intent.effect, 'unknown');
  assert.equal(requiresPlanGate(intent), true);
});

test('shell shadowing cannot earn an ephemeral exemption', () => {
  for (const value of [
    'rm(){ /tmp/evil; }; rm /tmp/x',
    'alias rm=evil; rm /tmp/x',
    'PATH=./bin:$PATH rm /tmp/x',
    './rm /tmp/x',
  ]) {
    const intent = writeIntent(command(value));
    assert.equal(requiresPlanGate(intent), true, value);
  }
});

test('only a proven read command bypasses plan-gate', () => {
  const intent = writeIntent(command('git status'));
  assert.equal(intent.effect, 'read');
  assert.equal(requiresPlanGate(intent), false);
});

test('core, not the adapter, owns the explicit session-effect allowlist', () => {
  for (const route of [
    'session.question', 'session.plan', 'session.work', 'session.schedule',
    'session.skill', 'session.delivery', 'session.ui', 'session.permission',
    'agent.control', 'plan.submit',
  ]) {
    const intent = writeIntent({ route, payload: {} });
    assert.equal(intent.effect, 'session', route);
    assert.equal(requiresPlanGate(intent), true, route);
  }
  assert.equal(requiresPlanGate(writeIntent({ route: 'session', payload: {} })), true,
    'an untyped adapter session assertion must not bypass core policy');
});

test('adapter metadata cannot grant permission to an unregistered agent', () => {
  const intent = writeIntent({
    route: 'agent.invoke',
    payload: { agentId: 'not-registered', declaredPermission: 'read-only' },
  });
  assert.equal(intent.effect, 'unknown');
  assert.equal(requiresPlanGate(intent), true);
});

test('malformed external payloads return unknown instead of throwing', () => {
  for (const action of [
    { route: 'file.mutate', payload: { target: {} } },
    { route: 'file.mutate', payload: { target: [] } },
    { route: 'file.patch', payload: { patch: {} } },
  ]) {
    let intent;
    assert.doesNotThrow(() => { intent = writeIntent(action); });
    assert.equal(intent.effect, 'unknown');
    assert.equal(requiresPlanGate(intent), true);
  }
});

test('a move from an ephemeral source to a permanent destination reaches plan-gate', () => {
  const intent = writeIntent({
    route: 'file.patch',
    payload: {
      changes: [{
        kind: 'update', file: '/tmp/staged.md', destination: 'README.md', oldText: 'old', newText: 'new',
      }],
    },
  });
  assert.equal(intent.effect, 'world');
  assert.deepEqual(intent.targets, ['README.md']);
  assert.equal(requiresPlanGate(intent), true);
});

test('only a typed plan artifact, not a plans directory name, is ephemeral', () => {
  const repositoryPlan = writeIntent({
    route: 'file.mutate', payload: { target: 'docs/plans/release.md', newText: '# release' },
  });
  assert.equal(repositoryPlan.effect, 'world');
  assert.equal(requiresPlanGate(repositoryPlan), true);

  const nativePlan = writeIntent({
    route: 'file.mutate',
    payload: {
      target: '/native/client/plans/main.md', newText: '# plan',
      planArtifact: { kind: 'plan', role: 'primary', path: '/native/client/plans/main.md' },
    },
  });
  assert.equal(nativePlan.effect, 'ephemeral');
  assert.equal(requiresPlanGate(nativePlan), true);

  const mismatched = writeIntent({
    route: 'file.mutate',
    payload: {
      target: 'docs/plans/release.md', newText: '# release',
      planArtifact: { kind: 'plan', role: 'primary', path: '/native/client/plans/main.md' },
    },
  });
  assert.equal(mismatched.effect, 'world');
});

test('lexical traversal cannot earn an ephemeral exemption', () => {
  for (const action of [
    { route: 'file.mutate', payload: { target: '/tmp/../repo/file' } },
    { route: 'file.mutate', payload: { target: 'scratchpad/../protected' } },
    command('rm /tmp/../repo/file'),
  ]) {
    const intent = writeIntent(action);
    assert.equal(requiresPlanGate(intent), true, JSON.stringify(action));
  }
});

test('an ephemeral mutation cannot hide an unproven command in the same chain', () => {
  const intent = writeIntent(command('rm /tmp/x; future-deployer --ship'));
  assert.equal(intent.effect, 'unknown');
  assert.equal(requiresPlanGate(intent), true);
});

test('an ephemeral mutation cannot hide a separate world mutation', () => {
  for (const value of [
    'rm /tmp/x; git push origin main',
    'touch /tmp/x && npm publish',
    "perl -i -pe 's/x/y/' /tmp/f; npm publish",
  ]) {
    const intent = writeIntent(command(value));
    assert.ok(['world', 'unknown'].includes(intent.effect), `${value}: ${intent.effect}`);
    assert.equal(requiresPlanGate(intent), true, value);
  }
});

test('an ephemeral redirect cannot bound a world mutation in the same simple command', () => {
  for (const value of [
    'git push origin main > /tmp/push.log',
    'npm publish > /tmp/publish.log',
  ]) {
    const intent = writeIntent(command(value));
    assert.ok(['world', 'unknown'].includes(intent.effect), `${value}: ${intent.effect}`);
    assert.equal(requiresPlanGate(intent), true, value);
  }
});

test('device paths are normalized before null-sink exemption', () => {
  const intent = writeIntent({
    route: 'file.mutate',
    payload: { target: '/dev/../Users/bonzarr/craft-local/README.md' },
  });
  assert.equal(intent.effect, 'world');
  assert.equal(requiresPlanGate(intent), true);
});

test('target-directory flags expose permanent ln and install destinations', () => {
  for (const value of [
    'ln -t repo /tmp/source',
    'ln --target-directory=repo /tmp/source',
    'install -t repo /tmp/source',
    'install --target-directory=repo /tmp/source',
  ]) {
    const intent = writeIntent(command(value));
    assert.equal(intent.effect, 'world', value);
    assert.equal(requiresPlanGate(intent), true, value);
  }
});

test('quoted ephemeral targets remain classified but still reach the model gate', () => {
  for (const value of ['rm "/tmp/foo bar"', 'echo $(date) > /tmp/stamp']) {
    const intent = writeIntent(command(value));
    assert.equal(intent.effect, 'ephemeral', value);
    assert.equal(requiresPlanGate(intent), true, value);
  }
});

test('temporary aliases cannot redirect writes into permanent paths', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'write-intent-link.'));
  const permanent = path.join(root, 'README.md');
  const link = path.join(base, 'repo-file');
  fs.symlinkSync(permanent, link);
  try {
    for (const action of [
      { route: 'file.mutate', payload: { target: link } },
      command(`echo changed > ${link}`),
      command(`ln -s ${root} /tmp/craft-link; echo changed > /tmp/craft-link/README.md`),
    ]) {
      const intent = writeIntent(action);
      if (action.route === 'command.run' && action.payload.command.includes('/tmp/craft-link;')) {
        assert.ok(['world', 'unknown'].includes(intent.effect), intent.effect);
      }
      assert.equal(requiresPlanGate(intent), true, JSON.stringify(action));
    }
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('dangling temporary symlinks cannot redirect creation into permanent paths', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'write-intent-dangling-link.'));
  const destination = path.join(root, 'not-created-by-test');
  const link = path.join(base, 'new-file');
  fs.symlinkSync(destination, link);
  try {
    const intent = writeIntent({ route: 'file.mutate', payload: { target: link } });
    assert.equal(intent.effect, 'world');
    assert.equal(requiresPlanGate(intent), true);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('sensitive gitignored files remain permanent', () => {
  const intent = writeIntent({
    route: 'file.mutate',
    payload: { target: path.join(root, '.env') },
  });
  assert.equal(intent.effect, 'world');
  assert.equal(requiresPlanGate(intent), true);
});

test('gitignore cannot make executable config and credential files ephemeral', () => {
  const repo = fs.mkdtempSync(path.join(root, '.test-sensitive.'));
  try {
    fs.writeFileSync(path.join(repo, '.gitignore'), '.envrc\n*.pem\n');
    spawnSync('git', ['init', '-q'], { cwd: repo });
    for (const file of ['.envrc', 'key.pem']) {
      const intent = writeIntent({ route: 'file.mutate', payload: { target: path.join(repo, file) } });
      assert.equal(intent.effect, 'world', file);
      assert.equal(requiresPlanGate(intent), true, file);
    }
  } finally {
    fs.rmSync(repo, { recursive: true, force: true });
  }
});

test('mutation parser preserves ephemeral targets behind wrappers and value flags', () => {
  for (const value of [
    'sudo rm /tmp/old',
    'TRACE=1 rmdir /tmp/empty',
    'unlink /tmp/link',
    'mkdir -m 755 /tmp/new',
    'chmod 755 /tmp/script',
    'chown root /tmp/file',
    'chgrp staff /tmp/file',
    'truncate -s 0 /tmp/file',
  ]) {
    const intent = writeIntent(command(value));
    assert.equal(intent.effect, 'ephemeral', value);
    assert.equal(requiresPlanGate(intent), true, value);
  }
});

test('source operands and edit programs are not mistaken for mutation targets', () => {
  for (const value of [
    'ln -s source /tmp/link',
    "sed -i '' 's/x/y/' /tmp/f",
    "perl -i -pe 's/x/y/' /tmp/f",
  ]) {
    const intent = writeIntent(command(value));
    assert.equal(intent.effect, 'ephemeral', value);
    assert.equal(requiresPlanGate(intent), true, value);
  }
});

test('tree mutators expose their permanent targets to the plan-gate', () => {
  for (const value of [
    'rmdir build',
    'unlink README.md',
    'shred secrets.txt',
    'git rm tracked.txt',
    'git mv old.txt new.txt',
  ]) {
    const intent = writeIntent(command(value));
    assert.equal(intent.effect, 'world', value);
    assert.ok(intent.targets.length > 0, value);
    assert.equal(requiresPlanGate(intent), true, value);
  }
});

test('write-intent CLI sends a canonical action to core', () => {
  const inspect = (route, value) => {
    const result = spawnSync(process.execPath, ['tools/write-intent.mjs', route, value], {
      cwd: root,
      encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };

  assert.equal(inspect('command.run', 'git status').effect, 'read');
  assert.equal(inspect('command.run', 'rm /tmp/x').effect, 'ephemeral');
  assert.equal(inspect('command.run', 'rm README.md').effect, 'world');
  assert.equal(inspect('file.mutate', '{"target":"/tmp/note"}').effect, 'ephemeral');
});
