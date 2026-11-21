// Server-runtime wiring for real single-source publication. Never import this
// from browser bundles. Missing configuration fails closed, with no fallback.
//
// Required Site runtime config (not request parameters):
//   OPENAI_API_KEY: approved server-side credential for this Site/project.
//   MEMORY_CONSTRUCT_MODEL: a Responses model ID supporting strict JSON schema.
//   MEMORY_REVIEW_MODEL: a distinct Responses model ID supporting that schema.
// Both calls send the authorized raw/source evidence to the fixed OpenAI
// Responses endpoint. store:false is requested; it does not promise provider
// zero retention. Only install where that transfer is explicitly permitted.
//
// authorizeSource(subject) must prove authorization for this EXACT input
// (id, immutable source bytes & SHA), this provider transfer and omission of
// transcript context. Caller must supply the real actively owned queue token.
// No chat consent, credentials, scope, model, URL or override comes from UI.
// This does not install routes or workers; it provides the bounded original
// single, unclocked source lane, not multi/context, repair or semantic linking.
import {createResponsesModel} from './model_https.mjs';
import {createLiveSourceCommitter} from './live_source_commit.mjs';
import {CANONICAL_MODEL_INSTRUCTIONS} from './source_model_prompt.mjs';

export function createLiveSourceProvider(env,{authorizeSource}={}) {
  if (!env || typeof env!=='object' || typeof authorizeSource!=='function')
    throw new TypeError('Trusted source provider runtime configuration is required.');
  // Explicit fields, not object spreads: no caller-supplied endpoints, keys,
  // transport hooks, fallback models or overrides of canonical instructions.
  const request=createResponsesModel({
    apiKey:env.OPENAI_API_KEY,
    constructModel:env.MEMORY_CONSTRUCT_MODEL,
    reviewModel:env.MEMORY_REVIEW_MODEL,
    instructions:CANONICAL_MODEL_INSTRUCTIONS,
    timeoutMs:20_000,maxOutputTokens:2048,
  });
  return createLiveSourceCommitter({
    authorizeSource,
    construct: async prompt=>request(prompt),
    review: async prompt=>request(prompt),
  });
}
