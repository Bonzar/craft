import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  drainClassifierNotices, formatClassifierNotices, recordClassifierNotice,
} from '../../core/classifier/notices.mjs';

test('classifier notices persist, deduplicate for the session, and drain once', () => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-notices.'));
  const env = { CRAFT_PERSISTENT_STATE_DIR: state, CRAFT_SESSION_ID: 'session-test' };
  try {
    const failed = {
      type: 'classifier_attempt_failed', backend: 'alpha', model: 'one',
      reason: 'usage_limit', retryAt: '15:52', detail: 'Лимит исчерпан',
    };
    recordClassifierNotice(failed, env);
    recordClassifierNotice(failed, env);
    recordClassifierNotice({ type: 'classifier_fallback_selected', backend: 'beta', model: 'two' }, env);
    const first = drainClassifierNotices(env);
    assert.deepEqual(first, [
      failed,
      { type: 'classifier_fallback_selected', backend: 'beta', model: 'two' },
    ]);
    assert.deepEqual(drainClassifierNotices(env), []);
    assert.match(formatClassifierNotices(first), /alpha\/one/);
    assert.match(formatClassifierNotices(first), /beta\/two/);
    assert.match(formatClassifierNotices(first), /лимит/i);
    assert.match(formatClassifierNotices(first), /15:52/);
  } finally {
    fs.rmSync(state, { recursive: true, force: true });
  }
});

test('all-candidates-unavailable notification names the attempted chain and fail-closed result', () => {
  const text = formatClassifierNotices([{
    type: 'classifier_unavailable',
    attempts: [
      { backend: 'alpha', model: 'one', reason: 'network' },
      { backend: 'beta', model: 'two', reason: 'auth' },
    ],
  }]);
  assert.match(text, /alpha\/one.*beta\/two/s);
  assert.match(text, /сет/i);
  assert.match(text, /авторизац/i);
  assert.match(text, /fail-closed/);
});

test('terminal unavailability is visible in the current process even after persistent dedupe', () => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-notices-terminal.'));
  const env = { CRAFT_PERSISTENT_STATE_DIR: state, CRAFT_SESSION_ID: 'session-terminal' };
  const notice = {
    type: 'classifier_unavailable',
    attempts: [{ backend: 'alpha', model: 'one', reason: 'timeout' }],
  };
  try {
    recordClassifierNotice(notice, env);
    drainClassifierNotices(env);
    recordClassifierNotice(notice, env);
    assert.deepEqual(drainClassifierNotices(env), [notice]);
  } finally {
    fs.rmSync(state, { recursive: true, force: true });
  }
});
