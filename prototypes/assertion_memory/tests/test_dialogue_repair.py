"""Automatic repair uses the same immutable sources and independent verifier."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.dialogue import extract_dialogue
from prototypes.assertion_memory.model import format_prompt
from prototypes.assertion_memory.tests.test_dialogue_recovery import event


class DialogueRepairTest(unittest.TestCase):
    def setUp(self):
        self.intro = event('intro', 'Я Марина Орлова, координатор лаборатории.', 'user')
        self.rooms = event('rooms', 'Лазурный — комната 73 с непрерывным питанием.', 'user')
        self.proposal = event('proposal', 'Предлагаю гасить свет ночью для экономии, только после проверки.')
        self.target = event('target', 'Да, кроме Лазурного. Схему фиксируем.', 'user')
        self.context = [self.intro, self.rooms, self.proposal, self.target]
        self.text = ('Марина Орлова согласовала гасить свет ночью для экономии, '
                     'кроме комнаты 73, только после проверки.')
        self.calls = []

    def candidate(self, *, identity=False, text=None):
        sources = [self.rooms, self.proposal, *([self.intro] if identity else [])]
        return {'text': text or self.text, 'quote': 'Да, кроме Лазурного.', 'kind': 'decision',
                'evidence': [{'event_id': source.id, 'quote': source.prompt} for source in sources]}

    def verifier(self, request, *, reject=False):
        verdicts = []
        for row in request.records:
            if row.get('role') != 'interpretation':
                continue
            refs = {ref['event_id'] for ref in row['evidence_refs']}
            status = 'unsupported' if reject else 'accepted' if self.intro.id in refs or row['text'] == 'Схему фиксируем.' else 'unresolved'
            verdicts.append({'index': row['index'], 'status': status,
                             'reason': 'Mandatory qualification omitted.' if reject else 'Identity source citation required.'})
        return json.dumps({'verdicts': verdicts})

    def provider(self, request):
        self.calls.append(request)
        self.assertLessEqual(len(format_prompt(request)), 60_000)
        if request.task.startswith('Extract'):
            return json.dumps({'assertions': [self.candidate(), 'Схему фиксируем.']})
        if request.task.startswith('Repair'):
            return json.dumps({'assertions': [self.candidate(identity=True)]})
        return self.verifier(request)

    def test_repairs_missing_identity_anchor_and_preserves_accepted_claim_once(self):
        result = extract_dialogue(self.provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], ['Схему фиксируем.', self.text])
        repaired = result['assertions'][-1]
        self.assertIn(self.intro.id, [ref['event_id'] for ref in repaired['evidence_refs']])
        self.assertEqual([row['status'] for row in result['reviews'] if row['kind'] == 'interpretation'], ['unresolved', 'accepted', 'accepted'])
        repairs = [call for call in self.calls if call.task.startswith('Repair')]
        self.assertEqual(len(repairs), 1)
        self.assertNotIn('Схему фиксируем.', [row.get('text') for row in repairs[0].records if row.get('role') == 'interpretation'])

    def test_unsupported_condition_exhausts_bounded_repair_and_remains_visible(self):
        def provider(request):
            self.calls.append(request)
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            if request.task.startswith(('Extract', 'Repair')):
                return json.dumps({'assertions': [self.candidate(identity=True, text='Гасим свет без проверки.')]})
            return self.verifier(request, reject=True)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual(result['assertions'], [])
        self.assertEqual(len([call for call in self.calls if call.task.startswith('Repair')]), 2)
        self.assertEqual(len([row for row in result['reviews'] if row['kind'] == 'interpretation']), 3)
        self.assertEqual(result['reviews'][-1]['kind'], 'source_coverage')
        self.assertEqual(result['reviews'][-1]['status'], 'unresolved')
        self.assertTrue(all(row['status'] == 'unsupported' for row in result['reviews'] if row['kind'] == 'interpretation'))
        self.assertIn('exhausted', str(result['context_recovery']))

    def test_repair_can_recover_missing_source_without_changing_raw_dialogue(self):
        context = [self.intro, *[event(i, 'Нейтральное обсуждение погоды.') for i in range(250)],
                   self.rooms, self.proposal, self.target]
        original = [(source.id, source.prompt) for source in context]
        def provider(request):
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            if request.task.startswith('Repair') and not any(row.get('id') == self.intro.id and row.get('text') for row in request.records):
                return json.dumps({'context_requests': [{'operation': 'search', 'query': 'Марина Орлова координатор лаборатории'}]})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, context)
        self.assertEqual(len(result['assertions']), 2)
        ref = next(ref for ref in result['assertions'][-1]['evidence_refs'] if ref['event_id'] == self.intro.id)
        self.assertEqual(self.intro.prompt[ref['start']:ref['end']], ref['quote'])
        self.assertEqual(original, [(source.id, source.prompt) for source in context])
        self.assertTrue(any(row['operation'] == 'search' for row in result['context_recovery']))

    def test_failed_repair_does_not_discard_already_accepted_claims_or_old_verdict(self):
        def provider(request):
            if request.task.startswith('Repair'):
                invalid = self.candidate(identity=True)
                invalid['evidence'][-1]['quote'] = 'Несуществующее представление.'
                return json.dumps({'assertions': [invalid]})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], ['Схему фиксируем.'])
        self.assertEqual(result['reviews'][0]['status'], 'unresolved')
        self.assertIn('absent', str(result['context_recovery']))

    def test_mixed_batch_repairs_once_and_does_not_resubmit_accepted_replacements(self):
        bad = self.candidate(identity=True, text='Гасим свет без проверки.')
        repair_indices = []
        def provider(request):
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [self.candidate(), bad, 'Схему фиксируем.']})
            if request.task.startswith('Repair'):
                indices = [row['index'] for row in request.records if row.get('role') == 'interpretation']
                repair_indices.append(indices)
                return json.dumps({'assertions': [{**(self.candidate(identity=True) if index == 0 else bad),
                                                  'index': index} for index in indices]})
            result = json.loads(self.verifier(request))
            claims = [row for row in request.records if row.get('role') == 'interpretation']
            for claim, verdict in zip(claims, result['verdicts']):
                if claim['text'] == bad['text']:
                    verdict.update(status='unsupported', reason='Mandatory qualification omitted.')
            return json.dumps(result)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual(repair_indices, [[0, 1], [1]])
        self.assertEqual([row['text'] for row in result['assertions']], ['Схему фиксируем.', self.text])
        self.assertEqual([row['status'] for row in result['reviews'] if row['kind'] == 'interpretation'],
                         ['unresolved', 'unsupported', 'accepted', 'accepted', 'unsupported', 'unsupported'])

    def test_repair_cannot_regenerate_accepted_text_under_a_different_kind(self):
        def provider(request):
            if request.task.startswith('Repair'):
                return json.dumps({'assertions': [self.candidate(text='Схему фиксируем.')]})
            return self.provider(request)
        result = extract_dialogue(provider, self.target, self.context)
        self.assertEqual([row['text'] for row in result['assertions']], ['Схему фиксируем.'])
        self.assertIn('already accepted', str(result['context_recovery']))

    def test_extraction_history_keeps_original_rejection_and_repair_verdict(self):
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.storage import MemoryStore
        with tempfile.TemporaryDirectory() as directory, MemoryStore(Path(directory) / 'toy.sqlite') as store:
            queue = CaptureQueue(store)
            sources = [queue.capture_event('s', row.id, row.prompt, actor=row.actor, source=row.source) for row in self.context]
            self.intro, self.rooms, self.proposal, self.target = sources
            # Work on the target only; preceding rows remain immutable context.
            extraction = extract_dialogue(self.provider, self.target, sources)
            claim = queue._claim(with_model=True)
            while claim.job.event_id != self.target.id:
                queue._finish_extraction(claim, queue.get_event(claim.job.event_id),
                                         {'assertions': [], 'reviews': [], 'context_event_ids': [], 'context_truncated': False})
                claim = queue._claim(with_model=True)
            queue._finish_extraction(claim, self.target, extraction)
            history = queue.extraction_history(self.target.id)
            self.assertEqual([row['status'] for row in history[0]['reviews'] if row['kind'] == 'interpretation'], ['unresolved', 'accepted', 'accepted'])
            self.assertEqual(len(queue.get(self.target.id).extracted_ids), 2)
            self.assertEqual([row.prompt for row in queue.list_events('s')], [row.prompt for row in self.context])


if __name__ == '__main__':
    unittest.main()
