"""Retention instructions are applied before construction, and again at verification."""
from ..dialogue import MATERIAL_REFERENT_RULES, SOURCE_RETENTION_RULES, _extraction_instructions, _verification_instructions
from ..source_coverage import _COVERAGE, _REPAIR
from ..thought_engine_contract import VERIFY, INDEPENDENT_SOURCE_REVIEW, construction_instructions


def test_source_admission_and_coverage_share_scope_and_attribution():
    for instructions in (construction_instructions(), _extraction_instructions(),
                         _verification_instructions(False), VERIFY, _COVERAGE):
        assert SOURCE_RETENTION_RULES in instructions
        assert 'only when extracting new durable source meaning' in instructions
        assert 'answering a question from admitted evidence or refreshing existing knowledge' in instructions
        assert 'Admission comes before attribution and context' in instructions
        assert 'Do not rescue ineligible material with an attributed meta-claim' in instructions
        assert 'concrete decision relevant to later steps' in instructions
        assert 'each assertion' in instructions
        assert 'derived conclusions with traceable premises' in instructions
        assert 'exact user-origin evidence' in instructions
        assert 'acceptance, execution and verified outcome' in instructions
        assert 'expiry and uncertainty' in instructions
        assert 'forbidden details or expansion of scoped permission' in instructions
        assert 'materially change useful knowledge' in instructions
        assert 'an indirect account cannot stand in for an unavailable underlying source' in instructions
        assert 'independent evidence that materially changes confidence' in instructions
    assert 'applies only after admission' in MATERIAL_REFERENT_RULES
    assert 'reject unsupported status and retention-ineligible' in VERIFY
    assert 'retention-ineligible raw or task-only statement' in INDEPENDENT_SOURCE_REVIEW
    assert 'Do not flag raw-only, task-only or repeated material' in _COVERAGE
    assert 'retention-eligible omitted material meanings' in _REPAIR


def test_complete_capture_compares_novelty_in_one_call_with_local_evidence():
    import json
    from ..model import ModelRequest
    from ..source_model_batch import generate_source_drafts

    examples = (
        ('user', 'Call me Rowan.'),
        ('assistant', 'I will also use a checklist in my next reply.'),
        ('tool', 'Telescope repair completed; ticket 51 closed by the technician.'),
    )
    group = [(ModelRequest('Construct thought', [{'role': 'source', 'text': text, 'origin': {'role': role}}],
                           construction_instructions(), response_schema={'type': 'object'}),
              [{'event_id': f'event-{idx}'}]) for idx, (role, text) in enumerate(examples)]
    replies = {'source_1': {'thoughts': ['name preference']}, 'source_2': {'thoughts': []},
               'source_3': {'thoughts': ['ticket 51 completed']}}
    calls = []

    def generate(request, bindings):
        calls.append((request, bindings))
        return json.dumps(replies), 'run-fixture'

    result = generate_source_drafts(group, generate, exact=True, joint_capture=True)
    assert len(calls) == 1
    request, bindings = calls[0]
    assert SOURCE_RETENTION_RULES in request.instructions
    assert 'complete capture together' in request.instructions
    assert 'only the records of that source as evidence' in request.instructions
    assert bindings == [item[1][0] for item in group]
    assert [json.loads(d.body) for d in result] == list(replies.values())
    generate_source_drafts(group, generate, exact=True)
    assert 'Apply these rules to each source independently' in calls[-1][0].instructions
    assert SOURCE_RETENTION_RULES in calls[-1][0].instructions


def test_missing_original_does_not_turn_a_memory_read_into_provenance():
    import json
    from ..model import ModelRequest
    from ..source_model_batch import generate_source_drafts

    recap = 'Memory search said a prior utterance gave a name; results may be incomplete.'
    fresh = 'Independent deployment monitor: exact release 18 served successfully at 11:38.'
    originals = [{'role': 'source', 'text': recap, 'origin': {'role': 'assistant'}},
                 {'role': 'source', 'text': fresh, 'origin': {'role': 'tool'}}]
    requests = [(ModelRequest('Construct thought', [record], construction_instructions(),
                              response_schema={'type': 'object'}), [{'event_id': f'event-{i}'}])
                for i, record in enumerate(originals)]
    calls = []

    def stub(request, bindings):
        calls.append((request, bindings))
        return json.dumps({'source_1': {'thoughts': []},
                           'source_2': {'thoughts': [{'text': fresh, 'support_fragments': [0]}]}}), 'fake'

    drafts = generate_source_drafts(requests, stub, exact=True, joint_capture=True)
    assert len(calls) == 1
    assert json.loads(drafts[0].body)['thoughts'] == []
    assert json.loads(drafts[1].body)['thoughts'][0]['text'] == fresh
    for instructions in (calls[0][0].instructions, VERIFY, _COVERAGE):
        assert 'Different wording' in instructions
        assert 'an indirect account cannot stand in for an unavailable underlying source' in instructions
    assert 'no knowledge change' in _COVERAGE
