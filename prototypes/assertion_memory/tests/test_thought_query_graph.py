"""End-to-end central query routing, graph-only discovery and ready-version reuse."""
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.model import Candidate
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService


def navigation(action, quote):
    return {'action': action, 'quote': quote, 'question': None, 'text': 'The visible content identifies the required location.'}


class ThoughtQueryGraphTest(unittest.TestCase):
    def test_gap_not_found_returns_the_existing_thought_as_partial_without_construction(self):
        from .test_thought_engine import ScriptedModel
        text = 'The group chose the 19:40 showing.'
        gap = 'Did Vlad personally confirm the choice?'
        model = ScriptedModel(navigation('focus',text),
            {'verdict':'build','question':gap,'text':'The group decision does not prove Vlad took part.'})
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory,model)
            thought = service.thoughts.publish(text,metadata={'verification':'accepted'})
            with patch('prototypes.assertion_memory.thought_engine.ThoughtEngine.retrieve',
                       side_effect=[[thought],[]]) as retrieve:
                result=service.query('Which showing did Vlad and the group choose?')
            self.assertEqual(retrieve.call_args.args,(gap,))
            self.assertEqual(retrieve.call_args.kwargs['exclude_ids'],{thought.id})
            self.assertEqual(result['answer'],text)
            self.assertEqual(result['view_id'],thought.id)
            self.assertFalse(result['answer_accepted'])
            self.assertTrue(result['partial_answer'])
            self.assertEqual(result['completeness'],'incomplete')
            self.assertEqual(result['missing_question'],gap)
            self.assertEqual(len(service.thoughts.list_thoughts()),1)
            self.assertEqual([r.task for r in model.requests],['Navigate thought graph','Check ready thought'])

    def test_gap_uses_independent_support_only_if_it_resolves_the_gap(self):
        from .test_thought_engine import ScriptedModel, ACCEPT
        text = 'The group chose the 19:40 showing.'
        gap = 'Did Vlad personally confirm the choice?'
        confirmation = 'Vlad said: I confirm the 19:40 showing.'
        evidence={'verification':'accepted'}
        for supported in (False,True):
            with self.subTest(supported=supported), MemoryStore(':memory:') as memory:
                surrounding = confirmation if supported else 'The theatre seats 60 people.'
                model = ScriptedModel(navigation('focus',text),
                    {'verdict':'lookup','question':gap,'text':'Check who confirmed the choice.'},
                    {'verdict':'build' if supported else 'partial','question':None if supported else gap,
                     'text':'The opened source is checked.'},
                    *([{'thought':{'text':text+' '+confirmation,'question':None,'support_fragments':[0,1]},
                        'needs_detail':None},ACCEPT] if supported else []))
                service = ThoughtService(memory,model)
                candidate=service.thoughts.publish(text,metadata=evidence)
                independent=service.thoughts.publish(surrounding,metadata=evidence)
                with patch('prototypes.assertion_memory.thought_engine.ThoughtEngine.retrieve',
                           side_effect=[[candidate],[independent]]):
                    result=service.query('Which showing did Vlad and the group choose?')
                packet=model.requests[2].records
                self.assertTrue(any(r.get('role')=='gap_question' and r.get('text')==gap for r in packet))
                self.assertTrue(any(r.get('role')=='gap_evidence' and r.get('text')==surrounding for r in packet))
                self.assertEqual(result['answer_accepted'],supported)
                self.assertEqual(len(service.thoughts.list_thoughts()),3 if supported else 2)
                if supported:
                    self.assertEqual(result['answer'],text+' '+confirmation)
                    self.assertEqual({b['thought_id'] for b in result['thought']['bases']},{candidate.id,independent.id})
                else:
                    self.assertTrue(result['partial_answer'])
                    self.assertEqual(result['answer'],text)
                    self.assertEqual(len(model.requests),3)

    def test_unknown_graph_route_does_not_discard_already_found_pending_source(self):
        from prototypes.assertion_memory.tests.test_thought_engine import ScriptedModel, ACCEPT
        model = ScriptedModel({'action': 'unknown', 'quote': None, 'question': None,
            'text': 'No matching thought exists in this graph yet.'},
            {'thought': {'text': 'The team chose the amber route.', 'question': None,
                         'support_fragments': [0]}, 'needs_detail': None}, ACCEPT)
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory, model)
            other = service.thoughts.publish('Unrelated garden supplies.')
            source = service.queue.capture_event('fixture', 'new', 'The team chose the amber route.', source='fixture')
            # The semantic-search miss is controlled; navigation and answer construction are real.
            with patch('prototypes.assertion_memory.thought_engine.ThoughtEngine.retrieve', return_value=[other]):
                result = service.query('Which route did the team choose?')
            self.assertTrue(result['answer_accepted'])
            self.assertEqual(result['thought']['bases'][0]['event_id'], source.id)

    def test_missing_questions_cannot_cycle_across_frames_on_unchanged_raw_material(self):
        from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded
        from prototypes.assertion_memory.model import SemanticUnavailable
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory)
            raw = service.queue.capture_event('fixture', 'turn', 'One fixed admitted source packet.', source='fixture')
            packet = service.engine._raw_packet(raw.id)
            material = ([record for record, _ in packet], [basis for _, basis in packet])
            asked = []
            def construct(*args, question=None, **kwargs):
                asked.append(question)
                if len(asked) > 2:
                    self.fail('missing questions cycled without new evidence')
                raise ThoughtDetailNeeded('B' if question == 'A' else 'A', 'The other detail is missing.')
            with patch.object(service.engine, 'retrieve', return_value=[]), \
                 patch.object(service.engine, '_pending_raw_material', return_value=material), \
                 patch.object(service.engine, '_construct', side_effect=construct):
                with self.assertRaises(SemanticUnavailable):
                    service.engine.answer('A')
            # B may answer the same evidence, but cannot send it back for another drill.
            self.assertEqual(asked, ['A', 'B'])

    def test_paraphrased_detail_chain_cannot_restart_unchanged_support(self):
        from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded
        from prototypes.assertion_memory.model import SemanticUnavailable
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory)
            raw = service.queue.capture_event('fixture', 'turn', 'One fixed source.', source='fixture')
            packet = service.engine._raw_packet(raw.id)
            material = ([record for record, _ in packet], [basis for _, basis in packet])
            asked = []
            def construct(*args, question=None, **kwargs):
                asked.append(question)
                if len(asked) > 2:
                    self.fail('new wording restarted an unchanged evidence packet')
                raise ThoughtDetailNeeded('More specific wording ' + str(len(asked)), 'The detail is still missing.')
            with patch.object(service.engine, 'retrieve', return_value=[]), \
                 patch.object(service.engine, '_pending_raw_material', return_value=material), \
                 patch.object(service.engine, '_construct', side_effect=construct):
                with self.assertRaises(SemanticUnavailable):
                    service.engine.answer('Original question')
            self.assertEqual(len(asked), 2)

    def test_different_detail_question_can_answer_the_same_evidence(self):
        from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory)
            raw = service.queue.capture_event('fixture', 'turn', 'Inspection is required.', source='fixture')
            packet = service.engine._raw_packet(raw.id)
            material = ([record for record, _ in packet], [basis for _, basis in packet])
            resolved = service.thoughts.publish('Inspection is required.', metadata={'verification': 'accepted'})
            asked = []
            def construct(*args, question=None, **kwargs):
                asked.append(question)
                if asked == ['Original question']:
                    raise ThoughtDetailNeeded('Which inspection?', 'Check the specific condition.')
                return resolved, ()
            with patch.object(service.engine, 'retrieve', return_value=[]), \
                 patch.object(service.engine, '_pending_raw_material', return_value=material), \
                 patch.object(service.engine, '_construct', side_effect=construct):
                self.assertEqual(service.engine.answer('Original question'), resolved)
            self.assertEqual(asked, ['Original question', 'Which inspection?', 'Original question'])

    def test_detail_frames_reuse_initial_pending_sources_without_searching_again(self):
        from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded
        from prototypes.assertion_memory.model import SemanticUnavailable
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory)
            sources = [service.queue.capture_event('fixture', str(index), text, source='fixture')
                       for index, text in enumerate(('Inspection is required.', 'Mira signs the inspection.'))]
            packets = [service.engine._raw_packet(source.id) for source in sources]
            material = [([record for record, _ in packet], [basis for _, basis in packet]) for packet in packets]
            asked = []
            def construct(*args, question=None, **kwargs):
                asked.append(question)
                if len(asked) > 2:
                    self.fail('a detail frame reran source discovery')
                raise ThoughtDetailNeeded('Detail ' + str(len(asked)), 'Read the existing source.')
            with patch.object(service.engine, 'retrieve', return_value=[]) as retrieve, \
                 patch.object(service.engine, '_pending_raw_material', side_effect=material) as pending, \
                 patch.object(service.engine, '_construct', side_effect=construct):
                with self.assertRaises(SemanticUnavailable):
                    service.engine.answer('Original question')
            self.assertEqual(asked, ['Original question', 'Detail 1'])
            retrieve.assert_called_once_with('Original question', ())
            pending.assert_called_once_with('Original question')

    def test_detail_frames_reopen_discovered_graph_locations_and_parent_full_material(self):
        from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded
        from prototypes.assertion_memory.thought_navigation import NavigationResult
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory)
            seed = service.thoughts.publish('Initial navigation description.')
            parent = service.thoughts.publish('Discovered parent.\n\nFull parent condition.')
            detail = service.thoughts.publish('Discovered detail.\n\nFull exception.')
            routes, prepared = [], []
            def route(engine, question, explicit=(), **kwargs):
                routes.append(kwargs['initial'])
                return NavigationResult(parent, (seed, parent), 'fixture') if len(routes) == 1 else NavigationResult(detail, (), 'fixture')
            def prepare(engine, question, selected, **kwargs):
                prepared.append((selected, kwargs['additional']))
                if len(prepared) == 1:
                    raise ThoughtDetailNeeded('What exception applies?', 'Open the linked detail.')
                return detail
            with patch.object(service.engine, 'retrieve', return_value=[seed]) as retrieve, \
                 patch.object(service.engine, '_pending_raw_material', return_value=((), ())) as pending, \
                 patch('prototypes.assertion_memory.thought_query.navigate', side_effect=route), \
                 patch('prototypes.assertion_memory.thought_query.answer_selected', side_effect=prepare):
                self.assertEqual(service.engine.answer('Original question'), detail)
            self.assertEqual(retrieve.call_count, 1)
            self.assertEqual(pending.call_count, 1)
            self.assertIn(parent, routes[1])
            self.assertIn(parent, prepared[1][1])

    def test_same_question_detail_reuses_complete_answer_without_copying_it(self):
        from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded
        from prototypes.assertion_memory.thought_navigation import NavigationResult
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory)
            question = 'What is known about readiness?'
            hint = service.thoughts.publish('An unverified route.', metadata={'verification': 'legacy_unverified'})
            answer = service.thoughts.publish('Readiness requires inspection.', question=question,
                metadata={'verification': 'accepted', 'protocol': 'unified-thought-v1'})
            routes = iter([hint, answer])
            checked = []
            def route(*args, **kwargs):
                return NavigationResult(next(routes), (), 'fixture route')
            def prepare(engine, asked, selected, **kwargs):
                checked.append(selected.id)
                if selected.id == hint.id:
                    raise ThoughtDetailNeeded(asked, 'Read the supporting source')
                return selected
            with patch('prototypes.assertion_memory.thought_query.navigate', side_effect=route), \
                 patch('prototypes.assertion_memory.thought_query.answer_selected', side_effect=prepare):
                result = service.query(question)
            self.assertEqual(result['thought']['id'], answer.id)
            self.assertEqual(checked, [hint.id, answer.id, answer.id])
            self.assertEqual(len(service.thoughts.list_thoughts()), 2)

    def test_query_follows_incoming_campus_connection_and_answers_only_from_selected_neighborhood(self):
        question = 'What must happen before opening the campus?'
        steps = iter([navigation('inspect', 'Management responsibilities.'),
            navigation('inspect', 'The campus connects management and readiness.'),
            navigation('focus', 'Opening requires the signed inspection report.'),
            {'verdict': 'ready', 'question': None, 'text': 'The selected content states the required condition.'}])
        requests = []
        def model(request):
            requests.append(request)
            return json.dumps(next(steps))
        class InitialManagement:
            def rank_relevance(self, query, corpus, *, limit=10):
                if not corpus:
                    return []
                key = next(key for key in corpus if json.loads(key)[0] == 'management')
                return [Candidate(key, 1.0, 'fixture-embedding')]
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory, model, InitialManagement())
            management = service.thoughts.publish('Management responsibilities.', thought_id='management')
            ready = service.thoughts.publish('Opening requires the signed inspection report.', question=question,
                metadata={'verification': 'accepted', 'protocol': 'unified-thought-v1'})
            service.thoughts.publish('The campus connects management and readiness.',
                bases=[{'thought_id': management.id}, {'thought_id': ready.id}])
            service.thoughts.publish('UNRELATED FAR PRIVATE MATERIAL.', bases=[{'thought_id': management.id}])
            result = service.query(question)
            self.assertTrue(result['answer_accepted'])
            self.assertEqual(result['thought']['id'], ready.id)
            self.assertEqual(result['answer'], ready.text)
            self.assertEqual([request.task for request in requests], ['Navigate thought graph'] * 3 + ['Check ready thought'])
            self.assertNotIn('UNRELATED FAR PRIVATE MATERIAL', json.dumps(requests[-1].records))
            self.assertIn(question, result['thought']['questions'])
            self.assertNotIn('question', result['thought'])

    def test_unchanged_question_reuses_version_without_model_and_question_record_does_not_grow(self):
        question = 'What is required before opening?'
        text = 'Engineering approval is required before opening.'
        steps = iter([navigation('focus', text),
            {'verdict': 'ready', 'question': None, 'text': 'The complete condition is present.'}])
        calls = []
        def model(request):
            calls.append(request.task)
            return json.dumps(next(steps))
        with MemoryStore(':memory:') as memory:
            service = ThoughtService(memory, model)
            thought = service.thoughts.publish(text, question=question,
                metadata={'verification': 'accepted', 'protocol': 'unified-thought-v1'})
            self.assertIsNone(service.ready_query(question))
            first = service.query(question)
            direct = service.ready_query(question)
            self.assertEqual(direct['answer'],first['answer'])
            self.assertEqual(direct['view_id'],first['view_id'])
            self.assertEqual(direct['completeness'],first['completeness'])
            repeat = service.query(question)
            self.assertEqual(first['thought']['id'], thought.id)
            self.assertEqual(repeat['version_id'], first['version_id'])
            self.assertEqual(calls, ['Navigate thought graph', 'Check ready thought'])
            with memory.transaction() as db:
                rows = db.execute('SELECT proofs FROM memory_thought_reuse_checks').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(json.loads(rows[0][0])), 1)


if __name__ == '__main__':
    unittest.main()
