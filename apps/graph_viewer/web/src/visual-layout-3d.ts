import {coupleViewMembers} from './coupled-view-layout.ts';
import type {CoupledViewWork} from './coupled-view-layout.ts';
/** Viewer geometry only. Coordinates do not assert a Source relation or membership. */
export type VisualPoint3D = [number, number, number];
export interface VisualTheme3D { readonly id: string; readonly index: number; readonly members: ArrayLike<number> }
export interface VisualThemeAnchor3D { id: string; center: VisualPoint3D }
export interface VisualTopicBounds3D extends VisualThemeAnchor3D {
  index: number; radius: number; min: VisualPoint3D; max: VisualPoint3D; memberCount: number;
}
export interface PreviousVisualLayout3D {
  positions: Float32Array;
  nodeIds?: readonly string[];
  /** Ordinal reuse without Source IDs is permitted only inside the same snapshot. */
  sameSnapshot?: boolean;
  themeAnchors?: readonly VisualThemeAnchor3D[];
}
export interface VisualLayout3DInput {
  sourcePositions: Float32Array;
  edges: Uint32Array;
  edgeActive?: Uint8Array;
  nodeIds?: readonly string[];
  /** Caller supplies only actual selected_registry memberships, never inferred links. */
  themes: readonly VisualTheme3D[];
  /** All catalog/history topics may receive bounds without influencing placement. */
  boundThemes?: readonly VisualTheme3D[];
  previous?: PreviousVisualLayout3D;
  fixedPositions?: Float32Array;
  unit?: number;
}
export interface VisualLayout3D {
  positions: Float32Array;
  topicBounds: VisualTopicBounds3D[];
  /** Stable presentation seeds; topicBounds centers describe actual final members. */
  themeAnchors: VisualThemeAnchor3D[];
  bounds: { min: VisualPoint3D; max: VisualPoint3D };
  unit: number;
  identityMode: 'source-id' | 'source-position-with-snapshot-ties';
  diagnostics: {
    iterations: 4; nodeVisits: number; edgeVisits: number; membershipVisits: number;
    coincidentSourceNodes: number; reusedNodes: number; unassignedNodes: number;
    /** Stable IDs still do not guarantee invariant geometry after topology changes. */
    crossRevisionIdentity: boolean;
    coupledViews?: CoupledViewWork;
  };
}

const PASSES = 4;
function mix(value: number): number {
  value = Math.imul(value ^ (value >>> 16), 0x21f0aaad);
  value = Math.imul(value ^ (value >>> 15), 0x735a2d97);
  return (value ^ (value >>> 15)) >>> 0;
}
function hash(text: string, salt = 0): number {
  let value = 2166136261 ^ salt;
  for (let i = 0; i < text.length; i++) value = Math.imul(value ^ text.charCodeAt(i), 16777619);
  return mix(value);
}
function unitValue(seed: number, axis: number): number { return mix(seed ^ Math.imul(axis + 1, 0x9e3779b9)) / 0xffffffff; }
function volume(seed: number, radius: number): VisualPoint3D {
  const z = 2 * unitValue(seed, 0) - 1, angle = unitValue(seed, 1) * Math.PI * 2;
  const radial = Math.cbrt(unitValue(seed, 2)) * radius, xy = Math.sqrt(Math.max(0, 1 - z * z));
  return [Math.cos(angle) * xy * radial, Math.sin(angle) * xy * radial, z * radial];
}
function finitePoint(values: ArrayLike<number>, at = 0): boolean {
  return Number.isFinite(values[at]) && Number.isFinite(values[at + 1]) && Number.isFinite(values[at + 2]);
}
function checkedNode(value: number, count: number): void {
  if (!Number.isInteger(value) || value < 0 || value >= count) throw new Error('Invalid visual layout node ordinal');
}

/**
 * Four initial edge passes and up to sixteen coupled View passes. Iterative
 * node/edge/member work is linear, with deterministic member sorting and bounded
 * View-pair tests; there is no node-pair force.
 * Names, text, naming status and edge types are deliberately absent from this API.
 * Every Source ordinal has one output position even when themes overlap.
 * With no bulk Source IDs the coordinate seed is snapshot-local: only coincident
 * Source positions use an ordinal tie-break. It cannot promise revision stability.
 */
export function layoutVisual3D(input: VisualLayout3DInput): VisualLayout3D {
  const { sourcePositions, edges, edgeActive, nodeIds, themes, previous } = input;
  const fixed=input.fixedPositions;
  if(fixed&&(fixed.length!==sourcePositions.length||fixed.some(x=>!Number.isFinite(x))))throw new Error('Invalid owner graph placement');
  const boundThemes = input.boundThemes ?? themes;
  const count = sourcePositions.length / 3, unit = input.unit ?? 1000;
  if (!Number.isInteger(count) || edges.length % 2 || !Number.isFinite(unit) || unit <= 0 || unit > 1e25) throw new Error('Invalid visual layout dimensions');
  if (nodeIds && nodeIds.length !== count) throw new Error('Visual layout Source ID count mismatch');
  if (edgeActive && edgeActive.length !== edges.length / 2) throw new Error('Visual layout edge activity count mismatch');
  const diagnostics: VisualLayout3D['diagnostics'] = { iterations: PASSES, nodeVisits: 0, edgeVisits: 0, membershipVisits: 0, coincidentSourceNodes: 0, reusedNodes: 0, unassignedNodes: 0, crossRevisionIdentity: Boolean(nodeIds) };
  const seeds = new Uint32Array(count), positions = new Float32Array(sourcePositions.length);
  const sourceIdsSeen = nodeIds ? new Set<string>() : null;
  const bits = new Uint32Array(sourcePositions.buffer, sourcePositions.byteOffset, sourcePositions.length);
  const coordinateHeads = nodeIds ? null : new Map<string, number>();
  const tied = nodeIds ? null : new Uint8Array(count);
  for (let node = 0; node < count; node++) {
    diagnostics.nodeVisits++;
    if (!finitePoint(sourcePositions, node * 3)) throw new Error('Nonfinite Source position in visual layout');
    if (nodeIds) {
      const id = nodeIds[node]!;
      if (sourceIdsSeen!.has(id)) throw new Error('Duplicate visual layout Source ID');
      sourceIdsSeen!.add(id); seeds[node] = hash(id); continue;
    }
    const at = node * 3, seed = mix(bits[at]! ^ mix(bits[at + 1]!) ^ mix(bits[at + 2]! ^ 0x85ebca6b));
    seeds[node] = seed;
    // An exact fixed-width coordinate key avoids pair scans even when a million
    // points coincide or a numeric seed hash collides. Canonicalize signed zero.
    const key = `${sourcePositions[at] === 0 ? 0 : bits[at]},${sourcePositions[at + 1] === 0 ? 0 : bits[at + 1]},${sourcePositions[at + 2] === 0 ? 0 : bits[at + 2]}`;
    const match = coordinateHeads!.get(key) ?? -1;
    if (match >= 0) {
      if (!tied![match]) { seeds[match] = mix(seed ^ mix(match + 1)); tied![match] = 1; diagnostics.coincidentSourceNodes++; }
      seeds[node] = mix(seed ^ mix(node + 1)); tied![node] = 1; diagnostics.coincidentSourceNodes++;
    }
    if (match < 0) coordinateHeads!.set(key, node);
  }
  // Deduplicate each real membership list in O(M), without clearing N slots/theme.
  const seen = new Uint32Array(count), membershipCounts = new Uint32Array(count);
  const homes = new Float64Array(sourcePositions.length), reused = new Uint8Array(count);
  const oldAnchors = new Map(previous?.themeAnchors?.map(anchor => [anchor.id, anchor.center]));
  const themeAnchors: VisualThemeAnchor3D[] = [];
  const uniqueThemes = new Set<string>(), uniqueIndices = new Set<number>();
  for (let slot = 0; slot < themes.length; slot++) {
    const theme = themes[slot]!;
    if (uniqueThemes.has(theme.id) || uniqueIndices.has(theme.index)) throw new Error('Duplicate visual layout theme');
    uniqueThemes.add(theme.id); uniqueIndices.add(theme.index);
    const old = oldAnchors.get(theme.id), seed = hash(theme.id);
    const center: VisualPoint3D = old && finitePoint(old) ? [...old] : [0, 1, 2].map(axis => (unitValue(seed, axis) * 2 - 1) * 3 * unit) as VisualPoint3D;
    themeAnchors.push({ id: theme.id, center });
    const stamp = slot + 1;
    for (let i = 0; i < theme.members.length; i++) {
      diagnostics.membershipVisits++;
      const node = theme.members[i]!; checkedNode(node, count);
      if (seen[node] === stamp) continue;
      seen[node] = stamp; membershipCounts[node] = membershipCounts[node]! + 1;
      for (let axis = 0; axis < 3; axis++) homes[node * 3 + axis] = homes[node * 3 + axis]! + center[axis]!;
    }
  }
  const oldById = previous?.nodeIds && nodeIds ? new Map(previous.nodeIds.map((id, ordinal) => [id, ordinal])) : null;
  for (let node = 0; node < count; node++) {
    diagnostics.nodeVisits++;
    const at = node * 3, memberships = membershipCounts[node]!;
    const jitter = volume(seeds[node]!, unit * (memberships > 1 ? .02 : memberships ? .1 : .01));
    for (let axis = 0; axis < 3; axis++) {
      // Fixed transform preserves the fallback's Source spatial order. No global
      // normalization changes the old map merely because a new extreme appears.
      const base = memberships ? homes[at + axis]! / memberships : Math.tanh(sourcePositions[at + axis]! / unit) * unit + (axis === 2 ? -5 * unit : 0);
      positions[at + axis] = base + jitter[axis]!;
      homes[at + axis] = positions[at + axis]!;
    }
    if (!memberships) diagnostics.unassignedNodes++;
    if(fixed){positions.set(fixed.subarray(at,at+3),at);reused[node]=1;diagnostics.reusedNodes++;continue;}
    const oldNode = oldById ? oldById.get(nodeIds![node]!) : previous?.sameSnapshot && !nodeIds ? node : undefined;
    if (oldNode !== undefined && previous && oldNode * 3 + 2 < previous.positions.length && finitePoint(previous.positions, oldNode * 3)) {
      positions.set(previous.positions.subarray(oldNode * 3, oldNode * 3 + 3), at);
      for (let axis = 0; axis < 3; axis++) homes[at + axis] = positions[at + axis]!;
      reused[node] = 1; diagnostics.reusedNodes++;
    }
  }
  const sums = new Float64Array(sourcePositions.length), degree = new Uint32Array(count);
  const visitEdges = (onlyThemedNeighbors: boolean): void => {
    sums.fill(0); degree.fill(0);
    for (let edge = 0; edge < edges.length / 2; edge++) {
      diagnostics.edgeVisits++;
      const from = edges[edge * 2]!, to = edges[edge * 2 + 1]!;
      checkedNode(from, count); checkedNode(to, count);
      if ((edgeActive && !edgeActive[edge]) || from === to) continue;
      if (!onlyThemedNeighbors || (!membershipCounts[from] && membershipCounts[to])) {
        degree[from] = degree[from]! + 1;
        for (let axis = 0; axis < 3; axis++) sums[from * 3 + axis] = sums[from * 3 + axis]! + positions[to * 3 + axis]!;
      }
      if (!onlyThemedNeighbors || (!membershipCounts[to] && membershipCounts[from])) {
        degree[to] = degree[to]! + 1;
        for (let axis = 0; axis < 3; axis++) sums[to * 3 + axis] = sums[to * 3 + axis]! + positions[from * 3 + axis]!;
      }
    }
  };
  // Unthemed nodes with actual themed neighbors start near those neighbors;
  // this is geometry only and never creates a membership or an edge.
  visitEdges(true);
  for (let node = 0; node < count; node++) {
    diagnostics.nodeVisits++;
    if (membershipCounts[node] || reused[node] || !degree[node]) continue;
    const jitter = volume(seeds[node]!, unit * .08);
    for (let axis = 0; axis < 3; axis++) {
      const at = node * 3 + axis;
      positions[at] = sums[at]! / degree[node]! + jitter[axis]!; homes[at] = positions[at]!;
    }
  }
  for (let pass = 0; !fixed&&pass < PASSES; pass++) {
    visitEdges(false);
    for (let node = 0; node < count; node++) {
      diagnostics.nodeVisits++;
      if (!degree[node]) continue;
      const at = node * 3;
      const dx = (sums[at]! / degree[node]! - positions[at]!) * .08 + (homes[at]! - positions[at]!) * .5;
      const dy = (sums[at + 1]! / degree[node]! - positions[at + 1]!) * .08 + (homes[at + 1]! - positions[at + 1]!) * .5;
      const dz = (sums[at + 2]! / degree[node]! - positions[at + 2]!) * .08 + (homes[at + 2]! - positions[at + 2]!) * .5;
      const limit = unit * (reused[node] ? .005 : .02), scale = Math.min(1, limit / Math.max(Number.EPSILON, Math.hypot(dx, dy, dz)));
      positions[at] = positions[at]! + dx * scale; positions[at + 1] = positions[at + 1]! + dy * scale; positions[at + 2] = positions[at + 2]! + dz * scale;
    }
  }
  if(!diagnostics.reusedNodes)diagnostics.coupledViews=coupleViewMembers(positions,themes,seeds,edges,edgeActive,unit,nodeIds);
  const topicBounds: VisualTopicBounds3D[] = [];
  seen.fill(0);
  const boundIds = new Set<string>(), boundIndices = new Set<number>();
  for (let slot = 0; slot < boundThemes.length; slot++) {
    const theme = boundThemes[slot]!, center: VisualPoint3D = [0, 0, 0], min: VisualPoint3D = [Infinity, Infinity, Infinity], max: VisualPoint3D = [-Infinity, -Infinity, -Infinity];
    if (boundIds.has(theme.id) || boundIndices.has(theme.index)) throw new Error('Duplicate visual layout bound theme');
    boundIds.add(theme.id); boundIndices.add(theme.index);
    let members = 0;
    for (let i = 0; i < theme.members.length; i++) {
      diagnostics.membershipVisits++;
      const node = theme.members[i]!; checkedNode(node, count);
      if (seen[node] === slot + 1) continue;
      seen[node] = slot + 1; members++;
      for (let axis = 0; axis < 3; axis++) { const value = positions[node * 3 + axis]!; center[axis] = center[axis]! + value; min[axis] = Math.min(min[axis]!, value); max[axis] = Math.max(max[axis]!, value); }
    }
    if (!members) continue;
    for (let axis = 0; axis < 3; axis++) center[axis] = center[axis]! / members;
    let radius = 0;
    // Repeated ordinals are harmless for a maximum and avoid another N clear.
    for (let i = 0; i < theme.members.length; i++) { diagnostics.membershipVisits++; const at = theme.members[i]! * 3; radius = Math.max(radius, Math.hypot(positions[at]! - center[0], positions[at + 1]! - center[1], positions[at + 2]! - center[2])); }
    topicBounds.push({ id: theme.id, index: theme.index, center, radius: Math.max(unit * .005, radius), min, max, memberCount: members });
  }
  const min: VisualPoint3D = count ? [Infinity, Infinity, Infinity] : [0, 0, 0], max: VisualPoint3D = count ? [-Infinity, -Infinity, -Infinity] : [0, 0, 0];
  for (let node = 0; node < count; node++) {
    diagnostics.nodeVisits++;
    for (let axis = 0; axis < 3; axis++) { const value = positions[node * 3 + axis]!; min[axis] = Math.min(min[axis]!, value); max[axis] = Math.max(max[axis]!, value); }
  }
  return { positions, topicBounds, themeAnchors, bounds: { min, max }, unit, identityMode: nodeIds ? 'source-id' : 'source-position-with-snapshot-ties', diagnostics };
}

export const buildVisualLayout3D = layoutVisual3D;
