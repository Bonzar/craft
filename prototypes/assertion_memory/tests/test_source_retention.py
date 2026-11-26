"""Retention instructions are applied before construction, and again at verification."""
from ..dialogue import SOURCE_RETENTION_RULES, _extraction_instructions, _verification_instructions
from ..source_coverage import _COVERAGE, _REPAIR
from ..thought_engine_contract import VERIFY, INDEPENDENT_SOURCE_REVIEW, construction_instructions


def test_source_admission_and_coverage_share_scope_and_attribution():
    for instructions in (construction_instructions(), _extraction_instructions(),
                         _verification_instructions(False), VERIFY, _COVERAGE):
        assert SOURCE_RETENTION_RULES in instructions
        assert 'each assertion' in instructions
        assert 'derived conclusions with traceable premises' in instructions
        assert 'exact user-origin evidence' in instructions
        assert 'acceptance, execution and verified outcome' in instructions
        assert 'expiry and uncertainty' in instructions
        assert 'forbidden details or expansion of scoped permission' in instructions
    assert 'reject unsupported status and retention-ineligible' in VERIFY
    assert 'retention-ineligible raw or task-only statement' in INDEPENDENT_SOURCE_REVIEW
    assert 'Do not flag retention-ineligible raw-only or task-only' in _COVERAGE
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
