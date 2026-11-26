"""Scripted contracts for the package-review prompt and fail-closed verdict handling."""
import json
from unittest import TestCase

from ..model import ModelError, ModelRequest
from ..source_model_batch import SourceDraft, verify_capture_group_coverage


class MaterialPackageCoverageTest(TestCase):
    user='Always use UTC in Orchard Lab status reports.'
    tool='Staging export check failed for candidate 730; the controller retained staging build 729 because of that failed check.'
    recap='In staging, 730 failed the export check, so build 729 remained. The report did not check production.'
    scope='Production deploy 731 was independently rejected by the production health controller.'

    def packet(self, *rows):
        cases, drafts = [], []
        for index,(role,text,retained) in enumerate(rows,1):
            cases.append((ModelRequest('Construct thought',[{'role':'source','actor_role':role,'text':text}],''), []))
            drafts.append(SourceDraft(json.dumps({'thoughts': [] if retained is None else [
                {'text':retained,'question':None,'support_fragments':[0]}],'needs_detail':None}),
                'fictional-run',f'source_{index}'))
        return cases,drafts

    def reviewer(self, verdict, reason, discard=()):
        def model(request, bindings):
            self.assertEqual(request.task,'Verify source thought batch')
            self.assertIn('underlying proposition, its material qualifiers, and the independently supported evidence',
                          request.instructions)
            self.assertIn('not separate evidence merely because it restates, interprets or labels',request.instructions)
            self.assertIn('does not require synthetic negative facts about what happened outside that scope',request.instructions)
            self.assertIn('Keep a separately supported causal finding or material scope restriction',request.instructions)
            self.assertIn('Do not convert co-occurrence, a recap, or absence of evidence into causation',request.instructions)
            return json.dumps(dict(verdict=verdict,text=reason,discard_drafts=list(discard))), 'fictional-run'
        return model

    def test_existing_scoped_causal_tool_finding_covers_assistant_recap(self):
        group,drafts=self.packet(('user',self.user,self.user),('tool',self.tool,self.tool),
                                 ('assistant',self.recap,None))
        actual=verify_capture_group_coverage(group,drafts,self.reviewer('accept','Scope and cause already preserved.'))
        self.assertEqual([json.loads(a.body) for a in actual],[json.loads(a.body) for a in drafts])

    def test_plain_narration_is_not_missing_material_coverage(self):
        narrative='I used the label Sparrow because I thought it was present in my conversation context.'
        group,drafts=self.packet(('assistant',narrative,None))
        actual=verify_capture_group_coverage(group,drafts,self.reviewer('accept','Only response mechanics.'))
        self.assertEqual(json.loads(actual[0].body)['thoughts'],[])

    def test_genuinely_new_supported_production_fact_is_not_a_repeat(self):
        group,drafts=self.packet(('user',self.user,self.user),('tool',self.tool,self.tool),('tool',self.scope,None))
        with self.assertRaisesRegex(ModelError,'omitted eligible meaning'):
            verify_capture_group_coverage(group,drafts,self.reviewer('reject','Independent real production outcome missing.'))

    def test_verified_cause_cannot_disappear_or_be_invented(self):
        merely_correlated='The staging export check failed for 730. Staging is serving build 729.'
        weakened='Staging build 729 remained.'
        invented='The staging export check failure caused production rollback.'
        group,drafts=self.packet(('tool',self.tool,weakened),('assistant',self.recap,None))
        with self.assertRaisesRegex(ModelError,'omitted eligible meaning'):
            verify_capture_group_coverage(group,drafts,self.reviewer('reject','Independently verified causal qualifier missing.'))
        group,drafts=self.packet(('tool',merely_correlated,merely_correlated),('assistant',self.recap,invented))
        actual=verify_capture_group_coverage(group,drafts,self.reviewer('accept','Discard unsupported causality and scope.',
                                                ({'source':'source_2','index':0},)))
        self.assertEqual(json.loads(actual[1].body)['thoughts'],[])
