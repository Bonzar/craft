import {
  isEphemeral,
  gitEphemeral,
  commandWriteTargets,
  commandMutationTargets,
  cleanTarget,
  normalizeTarget,
} from '../hooks/lib/write-targets.js';
import { canonicalPatchChanges } from '../contracts/action.mjs';
import { loadAgent } from '../agents/lib/registry.mjs';
import { classifyCommand, classifyCommandParts } from '../hooks/lib/read-only-command.js';

function targetState(targets, explicitlyEphemeral = []) {
  const clean = targets.filter((target) => typeof target === 'string')
    .map(cleanTarget).map(normalizeTarget).filter(Boolean);
  if (!clean.length) return { effect: 'read', targets: [] };
  const explicit = new Set(explicitlyEphemeral.map(cleanTarget).map(normalizeTarget).filter(Boolean));
  const permanent = clean.filter((target) => !explicit.has(target) && !isEphemeral(target) && !gitEphemeral(target));
  return permanent.length
    ? { effect: 'world', targets: permanent }
    : { effect: 'ephemeral', targets: clean };
}

function planArtifactTarget(input, targets) {
  const artifact = input.planArtifact;
  if (!artifact || typeof artifact !== 'object' || Array.isArray(artifact)) return [];
  if (artifact.kind !== 'plan' || !['primary', 'child'].includes(artifact.role)) return [];
  const artifactPath = normalizeTarget(cleanTarget(artifact.path));
  if (!artifactPath) return [];
  const normalizedTargets = targets.map(cleanTarget).map(normalizeTarget).filter(Boolean);
  return normalizedTargets.includes(artifactPath) ? [artifactPath] : [];
}

function agentPermission(input) {
  const id = String(input.agentId || '');
  try {
    return loadAgent(id).permission;
  } catch {
    return '';
  }
}

const SESSION_EFFECTS = new Set([
  'session.question',
  'session.plan',
  'session.work',
  'session.schedule',
  'session.skill',
  'session.delivery',
  'session.ui',
  'session.permission',
  'agent.control',
  'plan.submit',
  'none',
]);

export function writeIntent(action = {}) {
  const envelope = action && typeof action === 'object' && !Array.isArray(action) ? action : {};
  const route = typeof envelope.route === 'string' ? envelope.route : 'unknown';
  const input = envelope.payload && typeof envelope.payload === 'object' && !Array.isArray(envelope.payload)
    ? envelope.payload
    : {};
  if (route === 'read') return { effect: 'read', reason: 'adapter-proven read action', targets: [] };
  if (SESSION_EFFECTS.has(route)) {
    return { effect: 'session', reason: 'session state only', targets: [] };
  }
  if (route === 'agent.invoke' && agentPermission(input) === 'read-only') {
    return { effect: 'read', reason: 'registered read-only agent', targets: [] };
  }

  if (route === 'data.mutate') {
    const command = String(input.command || '');
    const ids = [...new Set(command.match(/[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}/g) || [])].sort();
    return { effect: 'world', reason: 'database write', targets: ids, category: 'database', ids };
  }

  if (route === 'file.mutate') {
    const target = typeof input.target === 'string' ? input.target : '';
    if (!target.trim()) return { effect: 'unknown', reason: 'file tool without target', targets: [] };
    return { ...targetState([target], planArtifactTarget(input, [target])), reason: 'file mutation', category: 'file' };
  }

  if (route === 'file.patch') {
    const changes = canonicalPatchChanges(input);
    if (!changes) return { effect: 'unknown', reason: 'patch payload is not canonical', targets: [] };
    if (!changes.length) return { effect: 'unknown', reason: 'patch without parsed files', targets: [] };
    const targets = changes.flatMap((change) => [change.file, change.destination].filter(Boolean));
    return { ...targetState(targets, planArtifactTarget(input, targets)), reason: 'multi-file mutation', category: 'patch', changes };
  }

  if (route === 'command.run') {
    const command = String(input.command || '');
    if (input.unbounded || !command) {
      return { effect: 'unknown', reason: 'unbounded command transport', targets: [], category: 'command' };
    }
    const parsed = commandWriteTargets(command);
    const mutation = commandMutationTargets(command);
    if (mutation.unsafeSyntax) {
      return { effect: 'unknown', reason: 'command may shadow or synthesize executables', targets: [], category: 'command' };
    }
    const state = targetState([...parsed, ...mutation.targets]);
    if (mutation.unknownMutation && state.effect !== 'world') {
      return { effect: 'world', reason: 'tree mutation without a bounded target', targets: [], category: 'command' };
    }
    const proof = classifyCommand(command);
    const proofParts = classifyCommandParts(command);
    const unprovenParts = proofParts
      .filter((part) => ['unproven', 'unparsed'].includes(part.cause)).length;
    const mutatingParts = proofParts.filter((part) => part.cause === 'mutates' && part.source !== 'redirect').length;
    if (state.effect === 'ephemeral'
      && unprovenParts + mutatingParts > mutation.boundedMutationCount) {
      const uncertain = unprovenParts > 0;
      return {
        effect: uncertain ? 'unknown' : 'world',
        reason: uncertain ? `command is ${proof.cause}` : 'command contains an unbounded mutation',
        targets: [],
        category: 'command',
      };
    }
    if (state.effect !== 'read') return { ...state, reason: 'command mutation', category: 'command' };
    if (proof.readOnly) return { effect: 'read', reason: 'command proven read-only', targets: [], category: 'command' };
    return {
      effect: proof.cause === 'mutates' ? 'world' : 'unknown',
      reason: `command is ${proof.cause || 'unproven'}`,
      targets: [],
      category: 'command',
    };
  }

  return { effect: 'unknown', reason: 'action is not proven read-only or session-only', targets: [] };
}

export function requiresPlanGate(intent) {
  return intent.effect !== 'read';
}
