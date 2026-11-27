"""Shared admission scope regression; semantic outcomes also have a fictional eval matrix."""
import json
from pathlib import Path
import unittest

from ..dialogue import _extraction_instructions, _verification_instructions
from ..source_coverage import _COVERAGE
from ..thought_engine_contract import VERIFY, construction_instructions


class AssistantNarrationAdmission(unittest.TestCase):
    def test_shared_pre_attribution_gate_reaches_extraction_and_verifiers(self):
        for prompt in (construction_instructions(), _extraction_instructions(),
                       _verification_instructions(False), VERIFY, _COVERAGE):
            self.assertIn('Before formulating or checking attribution, identify the independently material claim', prompt)
            self.assertIn('A qualification belongs only to an underlying claim that is itself eligible', prompt)
            self.assertIn('Retain direct durable user preferences or corrections and independently supported material findings', prompt)

    def test_fixture_matrix_keeps_real_scope_and_evidence_contrasts(self):
        fixtures=Path(__file__).with_name('fixtures')/'source_assistant_narration.json'
        cases={entry['id']:entry for entry in json.loads(fixtures.read_text())}
        self.assertEqual(cases['context_guess_is_raw']['expect'],'discard')
        self.assertEqual(cases['self_report_cannot_prove_effect']['expect'],'discard')
        self.assertEqual(cases['known_user_name_stays']['expect'],'retain')
        self.assertEqual(cases['context_guess_plus_user_correction']['expect'],'retain_only_user_preference')
        self.assertEqual(cases['independently_supported_outcome_stays']['messages'][0]['role'],'tool')
        self.assertEqual(cases['durable_user_report_about_agent_behavior_stays_scoped']['expect'],'retain')
