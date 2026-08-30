import assert from 'node:assert/strict';
import test from 'node:test';
import { parseCoverVerdict } from '../../core/classifier/verdict.mjs';

test('cover verdict schema accepts only exact one-line decisions', () => {
  for (const verdict of [
    'OVERRIDE:Ц1',
    'FORBIDDEN:Ц1: explicit ban',
    'COVERED:Ц1.2: implementation',
    'DRAFT',
    'UNCOVERED:not approved',
    'UNAVAILABLE',
  ]) assert.ok(parseCoverVerdict(verdict), verdict);

  for (const verdict of [
    'COVEREDNESS',
    'COVERED:',
    'COVERED:anything',
    'OVERRIDENONSENSE',
    'DRAFTjunk',
    'explanation\nCOVERED:Ц1.2: implementation',
    'COVERED:Ц1.2: implementation\ntrailing',
  ]) assert.equal(parseCoverVerdict(verdict), null, verdict);
});
