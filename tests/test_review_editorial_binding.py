"""The semantic reviewer judges a bound title; code supplies its technical identity."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import overview_review as review
from prototypes.assertion_memory.model import ModelRequest
from prototypes.assertion_memory.presentation_titles import sha


class ReviewEditorialBindingTest(unittest.TestCase):
    def ledger(self):
        return SimpleNamespace(visual_candidate={'display_title':'Fixture title','summary_sha256':sha('Fixture answer')},
            visual_review=None,source_gap_inventory=None,relation_gap_inventory=None,knowledge_at_cutoff=None,
            cache_context=None,manifests={},visible={},obligations={},reviewed_gaps=set())

    def run_review(self, visual, *, mutate_candidate=False):
        ledger = self.ledger()
        def contract(model,request,**kwargs):
            value={'status':'accepted','coverage':'all_claims','editorial_review':visual}
            if mutate_candidate:
                ledger.visual_candidate['display_title']='Changed after job dispatch'
            kwargs['observe'](value)
            return value
        with patch.object(review,'_packet_review_binding',return_value={'source_ids':[]}), \
             patch.object(review,'_make_review_request',return_value=ModelRequest('Review grounded memory overview',())), \
             patch.object(review,'_review_contract',side_effect=contract), \
             patch.object(review,'_coverage_needs',return_value=[]), \
             patch('prototypes.assertion_memory.source_omission.observe'):
            verdict=review._review_text(SimpleNamespace(model=object()),None,[],[],'Fixture answer',{},ledger,'global')
        return ledger.visual_review,verdict

    def test_reviewer_returns_only_judgment_and_code_binds_exact_title_and_text(self):
        visual,verdict=self.run_review({'accepted':True,'reason':'Describes the exact answer.'},mutate_candidate=True)
        self.assertIsNotNone(visual)
        self.assertEqual(visual['display_title'],'Fixture title')
        self.assertEqual(visual['summary_sha256'],sha('Fixture answer'))
        self.assertEqual(verdict['status'],'accepted')

    def test_reviewer_cannot_choose_or_echo_technical_binding_and_refusal_is_visual_only(self):
        for value in ({'accepted':True,'reason':'fixture','display_title':'Fixture title','summary_sha256':sha('Fixture answer')},
                      {'accepted':True,'reason':'fixture','summary_sha256':sha('Fixture answer')},
                      {'accepted':True,'reason':'fixture','display_title':'Fixture title'},
                      {'accepted':False,'reason':'Too broad.'}):
            with self.subTest(value=value):
                visual,verdict=self.run_review(value)
                self.assertIsNone(visual)
                self.assertEqual(verdict['status'],'accepted')

    def test_review_prompt_requests_no_technical_output_fields(self):
        with patch.object(review,'_metadata',return_value=[]):
            request=review._make_review_request([],[],'Fixture answer',{},self.ledger(),'global')
        self.assertIn('editorial_review={accepted:boolean,reason:brief}',request.instructions)
        self.assertNotIn('display_title:exact candidate',request.instructions)
        self.assertNotIn('summary_sha256:exact supplied hash',request.instructions)
