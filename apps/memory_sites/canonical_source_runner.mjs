/** Trusted worker factory for one actually admitted source with no context. */
import {createResponsesModel} from './model_https.mjs';
import {prepareCapturedSource} from './source_evidence.mjs';
import {CANONICAL_MODEL_INSTRUCTIONS, prepareInitialSourcePrompts} from './source_model_prompt.mjs';
import {createVerifiedWorker} from './verified_worker.mjs';

async function admittedWithoutContext(db, input) {
  // Conservative: no other preceding or following capture in this session.
  // Do not infer native transcript completeness from an empty local query.
  const [query] = await db.batch([db.prepare(`SELECT c.raw_json,c.raw_sha256
    FROM memory_raw_captures c WHERE c.event_id=?
    AND NOT EXISTS (SELECT 1 FROM memory_raw_captures n
      WHERE n.session_id=c.session_id AND n.event_id<>c.event_id)`).bind(input.id)]);
  if (query?.success === false || !Array.isArray(query?.results)) throw new Error('Source admission read failed.');
  const row = query.results[0];
  return row?.raw_json === input.source && row?.raw_sha256 === input.sourceSha256;
}

/**
 * model configuration comes from Site server settings, never the Site caller.
 * authorizeSource receives exact source bytes and must approve BOTH provider
 * processing and omission of all transcript context for this source. This
 * must be a trusted server policy based on verified source scope/authority.
 * Missing proof, multiple local session captures, clocked sources and repair
 * variants remain unavailable. This factory does not configure a live route.
 */
export function createCanonicalSourceRunner({model, authorizeSource, clock, leaseMs} = {}) {
  if (typeof authorizeSource !== 'function') throw new TypeError('Trusted source authorization is required.');
  const provider = createResponsesModel({...model,instructions:CANONICAL_MODEL_INSTRUCTIONS});
  async function admitted(db,input) {
    return await admittedWithoutContext(db,input) && await authorizeSource(input.source) === true;
  }
  return createVerifiedWorker({
    clock, leaseMs, canonicalInitialReview:true,
    async prepare(input,db) {
      if (!await admitted(db,input)) return {eligible:false};
      try {
        // Exclude unsupported origins before the construct adapter can call the provider.
        prepareInitialSourcePrompts(input);
        return prepareCapturedSource(input);
      } catch { return {eligible:false}; }
    },
    construct(input) {
      return provider(prepareInitialSourcePrompts(input).construct);
    },
    async review(input,db) {
      // Recheck source admission before a second external send and before
      // publishing evidence that was not evaluated with transcript context.
      if (!await admitted(db,input)) throw new Error('Source admission is unavailable for review.');
      const reply = await provider(prepareInitialSourcePrompts(input).review(input.candidateReply));
      if (!await admitted(db,input)) throw new Error('Source admission changed during review.');
      return reply;
    },
  });
}
