// Client-neutral list of files edited during the session. Adapters contribute
// mutations through the shared post-action ledger.
import fs from 'node:fs';
import { editedFilesState } from './paths.js';

function ledgerFiles() {
  try {
    return fs.readFileSync(editedFilesState(), 'utf8').split('\n').filter(Boolean);
  } catch {
    return [];
  }
}

export function sessionEditedFiles(adapterFiles = []) {
  const supplied = Array.isArray(adapterFiles) ? adapterFiles.filter((file) => typeof file === 'string' && file) : [];
  return [...new Set([...ledgerFiles(), ...supplied])].sort();
}
