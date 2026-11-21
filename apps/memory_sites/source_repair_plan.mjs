/**
 * Bounded pure plans: never a publication receipt, storage write or model call.
 * Trust only a prepared catalog and source/thought snapshots read by the server.
 */
import {validateModelReply} from './model_contract.mjs';

const nonSpace = /[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
const noControl = /^[^\u0000-\u001f]+$/u;
const maxRows = 128;
const maxBytes = 64 * 1024;
const enc = new TextEncoder();
const isObj = value => value !== null && typeof value === 'object' &&
  Object.getPrototypeOf(value) === Object.prototype;
const isText = value => typeof value === 'string' && value.isWellFormed() && value.length <= maxBytes;
const isId = value => isText(value) && value.length <= 512 && nonSpace.test(value) && noControl.test(value);
const version = value => Number.isSafeInteger(value) && value > 0;
const rows = value => Array.isArray(value) && value.length <= maxRows;
const invalid = () => { throw new TypeError('Invalid bounded source repair input.'); };
const draftShape = value => isObj(value) && isText(value.text) && nonSpace.test(value.text) &&
  (value.question === null || (isText(value.question) && nonSpace.test(value.question)));
const cleanDraft = ({text,question}) => ({text,question});
const draftKey = ({text,question}) => JSON.stringify([text,question]);
const within = value => isText(value) && enc.encode(value).byteLength <= maxBytes;

function validateDraftBatch(drafts) {
  if (!rows(drafts) || !drafts.every(draftShape) || !within(JSON.stringify(drafts.map(cleanDraft)))) invalid();
}

/**
 * Python ThoughtEngine._source_review_outcome, after the authoritative
 * independent Verify contract succeeds (also used for residual reviews with
 * zero or one draft). The initial zero/one-draft review uses a different
 * contract; this plan never authorizes publication.
 */
export function planIndependentSourceReview(drafts, reviewReply) {
  validateDraftBatch(drafts);
  if (!within(reviewReply)) invalid();
  validateModelReply('Verify thought',reviewReply,{sourceDraftCount:drafts.length});
  const review = JSON.parse(reviewReply);
  const rejected = new Map(review.rejected_drafts.map(row=>[row.draft,row.text]));
  const acceptedIndexes = drafts.flatMap((_row,index)=>rejected.has(index) ? [] : [index]);
  const feedback = [...rejected].map(([index,reason])=>({
    role:'rejected_draft', ...cleanDraft(drafts[index]), feedback:reason,
  }));
  if (review.missing_meaning !== null) feedback.push({role:'missing_meaning',text:review.missing_meaning});
  return {acceptedIndexes,feedback,next:feedback.length ? 'repair-residual' : 'complete'};
}

function feedbackValid(row) {
  if (!isObj(row)) return false;
  if (row.role === 'missing_meaning') return Object.keys(row).length === 2 && isText(row.text) && nonSpace.test(row.text);
  return row.role === 'rejected_draft' && Object.keys(row).length === 4 && draftShape(row) &&
    isText(row.feedback) && nonSpace.test(row.feedback);
}
function validateFeedback(feedback) {
  // Original independent review emits one feedback row per rejected draft
  // in reviewer-supplied order, then at most one missing_meaning row.
  if (!Array.isArray(feedback) || !feedback.length || feedback.length > maxRows+1 ||
      !feedback.every((row,index)=>feedbackValid(row) &&
        (row.role !== 'missing_meaning' || index === feedback.length-1)) ||
      !within(JSON.stringify(feedback))) invalid();
}

/**
 * Residual Construct receives previously accepted coverage but this coverage
 * and feedback are not added to the support catalog. Filter reparsed candidates
 * against accepted coverage and earlier candidates in the same repair round.
 */
export function planSourceRepair(acceptedDrafts, feedback) {
  validateDraftBatch(acceptedDrafts);
  validateFeedback(feedback);
  const priorKeys = new Set(acceptedDrafts.map(draftKey));
  const coverage = acceptedDrafts.map(draft=>({role:'accepted_coverage',...cleanDraft(draft)}));
  return {
    extraRecords:[...coverage, ...feedback.map(row=>({...row}))],
    selectNewIndexes(repairDrafts) {
      validateDraftBatch(repairDrafts);
      const seen = new Set(priorKeys);
      return repairDrafts.flatMap((draft,index)=>{
        const key = draftKey(draft);
        if (seen.has(key)) return [];
        seen.add(key);
        return [index];
      });
    },
  };
}

/**
 * Read-only guard equivalent to the original partial checkpoint pin checks
 * for bounded, structurally valid inputs. Current heads and context events
 * must have been read from authoritative storage; this does not lock them.
 */
export function sourceContinuationIsFresh({eventId,extractedIds,checkpoint,contextEvents,heads}={}) {
  if (!isId(eventId) || !rows(extractedIds) || !extractedIds.every(isId) || !isObj(checkpoint) ||
      !rows(checkpoint.accepted) || !rows(checkpoint.context_ids) || !checkpoint.context_ids.every(isId) ||
      !rows(contextEvents) || !rows(heads)) return false;
  try { validateFeedback(checkpoint.feedback); } catch { return false; }
  const {accepted,context_ids:ids} = checkpoint;
  return accepted.length === extractedIds.length && heads.length === accepted.length &&
    ids.length === contextEvents.length && ids.every((id,index)=>contextEvents[index]?.id === id) &&
    accepted.every((ref,index)=>isObj(ref) && isId(ref.thought_id) && version(ref.version) &&
      ref.thought_id === extractedIds[index] && heads[index]?.id === ref.thought_id &&
      heads[index]?.version === ref.version && heads[index]?.origin?.event_id === eventId);
}
