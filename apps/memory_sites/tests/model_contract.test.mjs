import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import { ModelContractError, validateModelReply } from '../model_contract.mjs';

const construct = 'Construct thought';
const verify = 'Verify thought';
const ready = 'Check ready thought';
const draft = {text: '  Проверка датчика обязательна.\u0085', question: null, support_fragments: [1, 1, 0]};
const goodReview = {verdict: 'accept', text: 'The draft retains the original prerequisite.'};
const detail = {question: 'Which sensor?', text: 'No source identifies it.'};
const sourceReview = {...goodReview, rejected_drafts: [], missing_meaning: null};
const acceptedDrafts = [{text: 'Проверка датчика обязательна.', question: null, support_fragments: [1, 0]}];

const cases = [];
const fixture = (task, response, options, accepts, normalized, literal = false) =>
  cases.push({task, body: literal ? response : JSON.stringify(response), options, accepts, normalized});

fixture(construct, {thoughts: [draft], needs_detail: null}, {supportCount: 2}, true, acceptedDrafts);
fixture(construct, {thoughts: [{...draft, question: undefined}]}, {supportCount: 2}, true, acceptedDrafts);
fixture(construct, {thoughts: []}, {supportCount: 2}, true, []);
fixture(construct, {thoughts: [draft]}, {supportCount: 1}, false);
fixture(construct, {thoughts: [{...draft, support_fragments: []}]}, {}, false);
fixture(construct, {thoughts: [{...draft, support_fragments: [true]}]}, {}, false);
fixture(construct, {thoughts: [{...draft, support_fragments: ['1']}]}, {}, false);
fixture(construct, '{"thoughts":[{"text":"x","support_fragments":[1.0]}]}', {}, false, undefined, true);
fixture(construct, '{"thoughts":[{"text":"x","support_fragments":[1e0]}]}', {}, false, undefined, true);
fixture(construct, '{"thoughts":[{"text":"x","support_fragments":[-0]}]}', {supportCount: 1}, true,
  [{text: 'x', question: null, support_fragments: [0]}], true);
fixture(construct, {thoughts: [{...draft, support_fragments: [-1]}]}, {}, false);
fixture(construct, {thoughts: [{...draft, source_id: 'model-supplied-id'}]}, {}, false);
fixture(construct, {thoughts: [{...draft, question: ' '} ]}, {}, false);
fixture(construct, {thoughts: [{...draft, text: '\u0085\u001d'}]}, {}, false);
fixture(construct, {thoughts: [{...draft, text: '\ufeff'}]}, {}, true,
  [{text: '\ufeff', question: null, support_fragments: [1, 0]}]);
fixture(construct, {thoughts: [{...draft}, {text: 'The window is shut.', support_fragments: [1]}]},
  {supportCount: 2}, true, [...acceptedDrafts, {text: 'The window is shut.', question: null, support_fragments: [1]}]);
fixture(construct, {needs_detail: detail}, {supportCount: 2}, true, []);
fixture(construct, {thoughts: [null], needs_detail: detail}, {supportCount: 2}, true, []);
fixture(construct, {verdict: 'needs_detail', ...detail}, {}, true, []);
fixture(construct, {verdict: 'needs_detail', ...detail, thoughts: []}, {}, false);
fixture(construct, {thought: draft, needs_detail: null}, {singleThought: true, supportCount: 2}, true, acceptedDrafts);
fixture(construct, {thought: draft}, {singleThought: true}, false);
fixture(construct, {thoughts: [draft], needs_detail: null}, {singleThought: true}, false);
fixture(construct, {thought: null, needs_detail: detail}, {singleThought: true, supportCount: 0}, true, []);
fixture(construct, {thought: null, needs_detail: detail}, {singleThought: true, supportCount: 2}, false);
fixture(construct, {thought: draft, needs_detail: detail}, {singleThought: true}, false);
fixture(construct, ['not an object'], {}, false);
fixture(verify, goodReview, {}, true);
fixture(verify, {...goodReview, verdict: 'reject'}, {}, true);
fixture(verify, {...goodReview, verdict: 'ready'}, {}, false);
fixture(verify, {...goodReview, open_support: null}, {}, false);
fixture(verify, goodReview, {reviewOptionCount: 2}, false);
fixture(verify, {...goodReview, open_support: null}, {reviewOptionCount: 2}, true);
fixture(verify, {...goodReview, verdict: 'drill', open_support: 1}, {reviewOptionCount: 2}, true);
fixture(verify, {...goodReview, verdict: 'drill', open_support: 2}, {reviewOptionCount: 2}, false);
fixture(verify, {...goodReview, verdict: 'drill', open_support: true}, {reviewOptionCount: 2}, false);
fixture(verify, {...goodReview, open_support: 0}, {reviewOptionCount: 2}, false);
fixture(verify, {...goodReview, meaning_changed: false}, {compareMeaning: true}, true);
fixture(verify, {...goodReview, meaning_changed: 0}, {compareMeaning: true}, false);
fixture(verify, {...goodReview, verdict: 'reject', meaning_changed: null}, {compareMeaning: true}, true);
fixture(verify, {...goodReview, verdict: 'reject', meaning_changed: true}, {compareMeaning: true}, false);
fixture(verify, sourceReview, {sourceDraftCount: 0}, true);
fixture(verify, {...sourceReview, missing_meaning: 'Another source meaning remains.'}, {sourceDraftCount: 2}, false);
fixture(verify, {...sourceReview, rejected_drafts: [{draft: 1, text: 'Unsupported.'}], verdict: 'reject'},
  {sourceDraftCount: 2}, true);
fixture(verify, {...sourceReview, rejected_drafts: [{draft: 2, text: 'Out of range.'}], verdict: 'reject'},
  {sourceDraftCount: 2}, false);
fixture(verify, {...sourceReview, rejected_drafts: [{draft: 1, text: 'One.'}, {draft: 1, text: 'Two.'}], verdict: 'reject'},
  {sourceDraftCount: 2}, false);
fixture(verify, {...sourceReview, open_support: 0, verdict: 'drill'}, {sourceDraftCount: 1, reviewOptionCount: 1}, true);
fixture(verify, {...sourceReview, open_support: 0, verdict: 'drill', missing_meaning: 'Omission'},
  {sourceDraftCount: 1, reviewOptionCount: 1}, false);
fixture(ready, {verdict: 'ready', text: 'Grounds are sufficient.'}, {}, true);
fixture(ready, {verdict: 'build', text: 'A new draft is needed.'}, {}, true);
fixture(ready, {verdict: 'drill', text: 'Need detail.', question: 'Which sensor?'}, {}, true);
fixture(ready, {verdict: 'drill', text: 'Need detail.', question: null}, {}, false);
fixture(ready, {verdict: 'accept', text: 'Not a ready-check verdict.'}, {}, false);
fixture('Verify source thought batch', goodReview, {}, true);
fixture('Verify source thought batch', sourceReview, {}, false);

test('canonical construction/review fixtures', () => {
  for (const {task, body, options, accepts, normalized} of cases) {
    const message = `${task} ${body} ${JSON.stringify(options)}`;
    if (accepts) assert.deepEqual(validateModelReply(task, body, options), normalized, message);
    else assert.throws(() => validateModelReply(task, body, options), ModelContractError, message);
  }
});

test('fixture expectations match authoritative Python validation', () => {
  const python = spawnSync('python3', ['-c', `
import json, sys
from prototypes.assertion_memory.thought_engine_contract import object_response, thought_drafts, validate_response
from prototypes.assertion_memory.model import ModelError
options = dict(supportCount='support_count', singleThought='single_thought', reviewOptionCount='review_option_count',
               sourceDraftCount='source_draft_count', compareMeaning='compare_meaning')
out = []
for row in json.load(sys.stdin):
    task = row['task']
    kwargs = {options[key]: value for key, value in row['options'].items()}
    try:
        response = object_response(row['body'])
        validate_response(task, response, **kwargs)
        normalized = None
        if task == 'Construct thought':
            normalized = [] if response.get('needs_detail') is not None or response.get('verdict') == 'needs_detail' else thought_drafts(response, **kwargs)
        out.append([True, normalized])
    except ModelError:
        out.append([False, None])
print(json.dumps(out, ensure_ascii=False))
`], {
    cwd: fileURLToPath(new URL('../../../', import.meta.url)),
    input: JSON.stringify(cases),
    encoding: 'utf8',
  });
  assert.equal(python.status, 0, python.error?.message ?? python.stderr);
  assert.deepEqual(JSON.parse(python.stdout), cases.map(row => [row.accepts, row.normalized ?? null]));
});

test('rejects invalid JSON and unsupported task names', () => {
  assert.throws(() => validateModelReply(construct, '{'), ModelContractError);
  assert.throws(() => validateModelReply(construct, {thoughts: []}), ModelContractError);
  assert.throws(() => validateModelReply('unimplemented', '{}'), TypeError);
});
