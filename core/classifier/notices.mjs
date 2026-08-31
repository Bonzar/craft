import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const memory = [];
const seenMemory = new Set();
const reasons = new Set([
  'usage_limit', 'network', 'timeout', 'auth', 'invalid_output', 'unsupported', 'unknown',
]);

function clean(value) {
  return String(value || '').replace(/[\r\n\t]/g, ' ').trim().slice(0, 160);
}

function candidate(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const backend = clean(value.backend);
  const model = clean(value.model);
  return backend && model ? { backend, model } : null;
}

function attempt(value) {
  const item = candidate(value);
  const reason = clean(value?.reason);
  if (!item || !reasons.has(reason)) return null;
  const detail = clean(value.detail);
  const retryAt = clean(value.retryAt);
  return {
    ...item, reason,
    ...(detail ? { detail } : {}),
    ...(retryAt ? { retryAt } : {}),
  };
}

function normalized(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const type = clean(value.type);
  if (type === 'classifier_attempt_failed') {
    const item = attempt(value);
    return item ? { type, ...item } : null;
  }
  if (type === 'classifier_fallback_selected') {
    const item = candidate(value);
    return item ? { type, ...item } : null;
  }
  if (type === 'classifier_unavailable') {
    const attempts = Array.isArray(value.attempts) ? value.attempts.map(attempt).filter(Boolean) : [];
    return { type, attempts };
  }
  if (type === 'classifier_configuration_invalid') return { type };
  return null;
}

function files(env) {
  const dir = clean(env.CRAFT_PERSISTENT_STATE_DIR);
  const session = clean(env.CRAFT_SESSION_ID);
  if (!dir || !session) return null;
  const id = crypto.createHash('sha256').update(session).digest('hex');
  return {
    queue: path.join(dir, `classifier-notices.${id}.jsonl`),
    seen: path.join(dir, `classifier-notices.${id}.seen`),
  };
}

function key(value) {
  return JSON.stringify(value);
}

export function recordClassifierNotice(value, env = process.env) {
  const notice = normalized(value);
  if (!notice) return false;
  const id = key(notice);
  const target = files(env);
  const isTerminal = notice.type === 'classifier_unavailable';
  if (isTerminal) memory.push(notice);
  if (!target) {
    if (seenMemory.has(id)) return isTerminal;
    seenMemory.add(id);
    if (!isTerminal) memory.push(notice);
    return true;
  }
  try {
    fs.mkdirSync(path.dirname(target.queue), { recursive: true });
    const seen = fs.existsSync(target.seen) ? new Set(fs.readFileSync(target.seen, 'utf8').split('\n').filter(Boolean)) : new Set();
    if (seen.has(id)) return isTerminal;
    fs.appendFileSync(target.seen, `${id}\n`);
    fs.appendFileSync(target.queue, `${JSON.stringify(notice)}\n`);
    return true;
  } catch {
    if (seenMemory.has(id)) return false;
    seenMemory.add(id);
    memory.push(notice);
    return true;
  }
}

export function drainClassifierNotices(env = process.env) {
  const values = memory.splice(0, memory.length);
  const target = files(env);
  if (target) {
    try {
      const lines = fs.readFileSync(target.queue, 'utf8').split('\n').filter(Boolean);
      for (const line of lines) {
        try {
          const notice = normalized(JSON.parse(line));
          if (notice) values.push(notice);
        } catch { /* malformed queue line is ignored */ }
      }
      fs.rmSync(target.queue, { force: true });
    } catch { /* no persisted notices */ }
  }
  const unique = new Map(values.map((value) => [key(value), value]));
  return [...unique.values()];
}

function name(value) {
  return `${value.backend}/${value.model}`;
}

const reasonLabels = {
  usage_limit: 'исчерпан лимит',
  network: 'сбой сети',
  timeout: 'таймаут',
  auth: 'ошибка авторизации',
  invalid_output: 'невалидный ответ',
  unsupported: 'не поддерживается',
  unknown: 'неизвестная ошибка',
};

function failedName(value) {
  const retry = value.retryAt ? `; повтор после ${value.retryAt}` : '';
  const detail = value.detail ? `; ${value.detail}` : '';
  return `${name(value)} — ${reasonLabels[value.reason]}${retry}${detail}`;
}

export function formatClassifierNotices(values) {
  const notices = Array.isArray(values) ? values.map(normalized).filter(Boolean) : [];
  if (!notices.length) return '';
  const unavailable = notices.find((value) => value.type === 'classifier_unavailable');
  if (unavailable) {
    const chain = unavailable.attempts.length ? unavailable.attempts.map(failedName).join(' → ') : 'нет доступных кандидатов';
    return `⚠️ Plan-gate: классификатор недоступен. Проверены: ${chain}. Вызов остаётся заблокирован fail-closed.`;
  }
  if (notices.some((value) => value.type === 'classifier_configuration_invalid')) {
    return '⚠️ Plan-gate: конфигурация цепочки классификатора невалидна. Вызов остаётся заблокирован fail-closed.';
  }
  const failed = notices.filter((value) => value.type === 'classifier_attempt_failed');
  const selected = notices.find((value) => value.type === 'classifier_fallback_selected');
  if (selected) return `⚠️ Plan-gate: ${failed.map(failedName).join(', ') || 'основная модель недоступна'}; используется fallback ${name(selected)}.`;
  return failed.length ? `⚠️ Plan-gate: временный сбой ${failed.map(failedName).join(', ')}; повторный вызов сработал.` : '';
}
