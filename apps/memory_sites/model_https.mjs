/** Isolated transport only. No prompt construction, model parity or publication. */
import {validateModelReply} from './model_contract.mjs';

const endpoint = 'https://api.openai.com/v1/responses';
const tasks = Object.freeze({
  'Construct thought': ['construct', 'construct_thought'],
  'Verify thought': ['review', 'verify_thought'],
  'Verify source thought batch': ['review', 'verify_source_thought_batch'],
  'Check ready thought': ['review', 'check_ready_thought'],
});

const errorText = Object.freeze({
  input: 'Invalid or oversized model request.',
  timeout: 'Model request timed out.',
  network: 'Model transport failed.',
  rate_limit: 'Model provider rate limited the request.',
  upstream: 'Model provider temporarily unavailable.',
  http: 'Model provider rejected the request.',
  protocol: 'Invalid model provider response.',
  oversized: 'Model provider response exceeds the byte limit.',
  incomplete: 'Model provider did not complete the response.',
  refusal: 'Model provider refused the request.',
  contract: 'Model reply does not satisfy the task contract.',
});

export class ModelHttpsError extends Error {
  constructor(code) {
    super(errorText[code]);
    this.name = 'ModelHttpsError';
    this.code = code;
    this.retryable = ['timeout', 'network', 'rate_limit', 'upstream'].includes(code);
  }
}

const fail = code => { throw new ModelHttpsError(code); };
const encoder = new TextEncoder();
const boundedText = (value, cap) => typeof value === 'string' && value.length <= cap &&
  value.isWellFormed() && encoder.encode(value).byteLength <= cap;
const exactInt = (value, low, high) => Number.isSafeInteger(value) && low <= value && value <= high;
const modelId = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$/.test(value);

function cancel(resource) {
  try { Promise.resolve(resource?.cancel()).catch(() => {}); } catch { /* best effort */ }
}

async function readJson(response, cap, signal) {
  const size = response.headers?.get('content-length');
  if (size != null && /^\d+$/.test(size) && Number(size) > cap) {
    cancel(response.body);
    fail('oversized');
  }
  if (!response.body || typeof response.body.getReader !== 'function') fail('protocol');
  const reader = response.body.getReader();
  const abort = () => cancel(reader);
  signal.addEventListener('abort', abort, {once: true});
  const decoder = new TextDecoder('utf-8', {fatal: true});
  let bytes = 0;
  let complete = false;
  const chunks = [];
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (signal.aborted) fail('timeout');
      if (done) break;
      if (!(value instanceof Uint8Array)) fail('protocol');
      bytes += value.byteLength;
      if (bytes > cap) fail('oversized');
      chunks.push(decoder.decode(value, {stream: true}));
    }
    chunks.push(decoder.decode());
    complete = true;
    return JSON.parse(chunks.join(''));
  } catch (error) {
    if (error instanceof ModelHttpsError) throw error;
    fail('protocol');
  } finally {
    signal.removeEventListener('abort', abort);
    if (!complete) cancel(reader);
    reader.releaseLock();
  }
}

function extract(body) {
  if (!body || typeof body !== 'object' || !Array.isArray(body.output)) fail('protocol');
  if (body.status !== 'completed' || body.incomplete_details != null || body.error != null) fail('incomplete');
  let result;
  for (const item of body.output) {
    if (item?.type === 'reasoning') continue;
    if (item?.type !== 'message' || item.role !== 'assistant' || item.status !== 'completed' ||
        !Array.isArray(item.content)) fail('protocol');
    for (const part of item.content) {
      if (part?.type === 'refusal') fail('refusal');
      if (part?.type !== 'output_text' || typeof part.text !== 'string' || result !== undefined) fail('protocol');
      result = part.text;
    }
  }
  if (result === undefined) fail('protocol');
  return result;
}

/**
 * Create a trusted, server-only provider. The key is injected from a runtime
 * secret and never returned. The caller owns task prompts, schema generation,
 * and model-version fit; no arguments here should come directly from HTTP/MCP.
 */
export function createResponsesModel({
  apiKey, constructModel, reviewModel, instructions, fetchImpl = globalThis.fetch,
  timeoutMs = 20_000, requestBytes = 96 * 1024, responseBytes = 96 * 1024, maxOutputTokens = 2048,
} = {}) {
  if (typeof apiKey !== 'string' || !/^[!-~]{1,512}$/.test(apiKey) ||
      !modelId(constructModel) || !modelId(reviewModel) || constructModel === reviewModel ||
      !boundedText(instructions, 24 * 1024) || !instructions.trim() ||
      typeof fetchImpl !== 'function' || !exactInt(timeoutMs, 1, 60_000) ||
      !exactInt(requestBytes, 1, 256 * 1024) || !exactInt(responseBytes, 1, 256 * 1024) ||
      !exactInt(maxOutputTokens, 16, 8192)) {
    throw new TypeError('Invalid server model configuration; distinct writer and reviewer models are required.');
  }

  return async function requestModel({task, prompt, schema, contract = {}} = {}) {
    if (!Object.hasOwn(tasks, task) || !boundedText(prompt, requestBytes) || !prompt.trim() ||
        !schema || schema.type !== 'object' || schema.additionalProperties !== false) fail('input');
    const [lane, name] = tasks[task];
    let body;
    try {
      body = JSON.stringify({
        model: lane === 'construct' ? constructModel : reviewModel,
        input: [{role: 'user', content: prompt}],
        instructions,
        store: false, background: false, stream: false, tools: [], tool_choice: 'none',
        max_output_tokens: maxOutputTokens,
        text: {format: {type: 'json_schema', strict: true, name, schema}},
      });
    } catch { fail('input'); }
    if (!boundedText(body, requestBytes)) fail('input');

    const controller = new AbortController();
    let timer;
    const stop = new Promise((_, reject) => {
      timer = setTimeout(() => {
        reject(new ModelHttpsError('timeout'));
        controller.abort();
      }, timeoutMs);
    });
    const perform = async () => {
      let response;
      try {
        response = await fetchImpl(endpoint, {
          method: 'POST', redirect: 'error', credentials: 'omit', cache: 'no-store', signal: controller.signal,
          headers: {'authorization': `Bearer ${apiKey}`, 'content-type': 'application/json', accept: 'application/json'},
          body,
        });
      } catch { fail('network'); }
      if (controller.signal.aborted) {
        cancel(response?.body);
        fail('timeout');
      }
      if (!response || response.redirected || (response.url && response.url !== endpoint)) {
        cancel(response?.body);
        fail('protocol');
      }
      if (response.status !== 200) {
        cancel(response.body); // Never parse/log provider error content.
        if (response.status === 429) fail('rate_limit');
        if (response.status >= 500 && response.status <= 599) fail('upstream');
        fail('http');
      }
      const reply = extract(await readJson(response, responseBytes, controller.signal));
      try { validateModelReply(task, reply, contract); } catch { fail('contract'); }
      return reply;
    };
    try { return await Promise.race([perform(), stop]); } finally { clearTimeout(timer); }
  };
}
