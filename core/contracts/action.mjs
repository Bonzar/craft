const PATCH_KINDS = new Set(['add', 'update', 'delete']);

export function canonicalPatchChanges(payload) {
  if (!payload || typeof payload !== 'object' || !Array.isArray(payload.changes)) return null;
  const changes = [];
  for (const value of payload.changes) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const keys = Object.keys(value).sort();
    const allowed = ['destination', 'file', 'kind', 'newText', 'oldText'];
    if (keys.some((key) => !allowed.includes(key))) return null;
    if (!PATCH_KINDS.has(value.kind) || typeof value.file !== 'string' || !value.file.trim()) return null;
    if (typeof value.oldText !== 'string' || typeof value.newText !== 'string') return null;
    if (value.destination !== undefined && (typeof value.destination !== 'string' || !value.destination.trim())) return null;
    changes.push({
      kind: value.kind,
      file: value.file,
      ...(value.destination === undefined ? {} : { destination: value.destination }),
      oldText: value.oldText,
      newText: value.newText,
    });
  }
  return changes;
}
