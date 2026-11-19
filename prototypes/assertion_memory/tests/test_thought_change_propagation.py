"""Only changed grounds or confirmed new material refresh an existing thought."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker


class ThoughtChangePropagationTest(unittest.TestCase):
    def test_search_candidates_require_semantic_confirmation_in_existing_model_call(self):
        with MemoryStore(':memory:') as memory:
            store, queue = ThoughtStore(memory), CaptureQueue(memory)
            event = queue.capture_event('s','user','Allow a short swipe only in the deck.',source='fixture')
            new = store.publish(event.prompt, origin={'event_id':event.id, 'kind':'source_interpretation'},
                                bases=[{'event_id':event.id}])
            relevant = store.publish('A swipe is available.',question='When is a short swipe allowed?')
            unrelated = store.publish('Use male gender when speaking.',question='How should the assistant speak?')
            calls = []
            def model(request):
                calls.append(request.task)
                options = [record for record in request.records if 'refresh_option' in record]
                selected = [record['refresh_option'] for record in options if record['text']==relevant.text]
                return json.dumps({'question':None,'text':'Only the swipe condition changed.',
                                   'affected_candidates':selected})
            engine = ThoughtEngine(store,model)
            engine.retrieve = lambda *args,**kwargs: [new,relevant,unrelated]
            worker = ThoughtWorker(queue,engine,lane='maintenance')
            worker.enqueue_changed([new])
            self.assertEqual(worker.run_once()['status'],'completed')
            self.assertEqual(worker.work.get(relevant.id)['action'],'refresh')
            self.assertIsNone(worker.work.get(unrelated.id))
            self.assertEqual(calls,['Find useful shared thought question'])

    def test_links_are_saved_before_refresh_and_overview_uses_only_linked_material(self):
        with MemoryStore(':memory:') as memory:
            store, queue = ThoughtStore(memory), CaptureQueue(memory)
            event = queue.capture_event('s', 'inspection', 'The hall inspection is complete.', source='fixture')
            new = store.publish(event.prompt, origin={'event_id': event.id, 'kind': 'source_interpretation'},
                                bases=[{'event_id': event.id}])
            status = store.publish('Inspection of the hall is still pending.', question='Is inspection done?')
            detail = store.publish('The hall is the venue for Friday.')
            unrelated = store.publish('The outdoor venue is unavailable.')
            question = 'Can the Friday venue be used after inspection?'
            worker = None

            def model(request):
                if request.task == 'Find useful shared thought question':
                    self.assertFalse(memory._writing)
                    rows = {record['text']: record for record in request.records}
                    return json.dumps({'question': question, 'text': 'Friday venue readiness',
                        'reuse_candidate': None, 'affected_candidates': [rows[status.text]['refresh_option']],
                        'relations': [
                            {'source': rows[new.text]['link_option'], 'target': rows[status.text]['link_option'],
                             'text': 'The completed inspection changes the pending status.'},
                            {'source': rows[new.text]['link_option'], 'target': rows[detail.text]['link_option'],
                             'text': 'The venue thought identifies which hall was inspected.'},
                        ]})
                if request.task == 'Construct thought':
                    self.assertEqual({edge['target_id'] for edge in store.links(new.id)}, {status.id, detail.id})
                    self.assertEqual(worker.work.get(status.id)['action'], 'refresh')
                    self.assertIsNone(worker.work.get(detail.id))
                    from .prepared_support_helpers import drafts
                    return json.dumps(drafts(request, ('The Friday venue passed inspection.',
                                                       [new.text, detail.text])))
                return json.dumps({'verdict': 'accept', 'text': 'This synthesis adds a distinct answer.',
                                   'meaning_changed': True})

            engine = ThoughtEngine(store, model)
            engine.retrieve = lambda *args, **kwargs: [new, status, detail, unrelated]
            worker = ThoughtWorker(queue, engine, lane='maintenance')
            worker.enqueue_changed([new])
            self.assertEqual(worker.run_once()['status'], 'completed')
            overview = next(row for row in store.list_thoughts() if row.question == question)
            self.assertEqual({basis['thought_id'] for basis in overview.bases}, {new.id, detail.id})
            self.assertIsNone(worker.work.get(unrelated.id))

    def test_semantically_covered_question_reuses_bound_thought_without_generation(self):
        with MemoryStore(':memory:') as memory:
            store = ThoughtStore(memory)
            changed = store.publish('The north building contains classrooms.')
            overview = store.publish('Classes take place in the north building.',
                                     question='Where are classes held?')
            calls = []

            def model(request):
                calls.append(request.task)
                option = next(record['reuse_option'] for record in request.records
                              if record['text'] == overview.text)
                return json.dumps({'question': None, 'text': 'Already covered.',
                                   'reuse_candidate': option})

            engine = ThoughtEngine(store, model)
            result = engine.consolidate([changed])
            self.assertEqual(result, [overview])
            self.assertEqual(calls, ['Find useful shared thought question'])
            self.assertEqual(store.versions(overview.id), [overview])
            self.assertEqual(len(store.list_thoughts()), 2)

    def test_distinct_question_can_use_an_existing_overview_as_one_ground(self):
        with MemoryStore(':memory:') as memory:
            store = ThoughtStore(memory)
            overview = store.publish('Rooms 214 and 216 in the north building are classrooms.',
                                     question='Where are the classrooms?')
            changed = store.publish('Ventilation in rooms 214 and 216 has not been checked.')
            question = 'Can classes begin in rooms 214 and 216?'
            calls = []

            def model(request):
                calls.append(request.task)
                if request.task == 'Find useful shared thought question':
                    return json.dumps({'question': question, 'text': 'Readiness of rooms 214 and 216',
                                       'reuse_candidate': None})
                if request.task == 'Construct thought':
                    self.assertIn('A different question may reuse', request.instructions)
                    return json.dumps({'thought': {'question': question,
                        'text': 'Rooms 214 and 216 are classrooms awaiting ventilation checks.',
                        'support_fragments': [0, 1]}, 'needs_detail': None})
                self.assertIn('Repeating a relevant fact across different answers is valid.', request.instructions)
                return json.dumps({'verdict': 'accept', 'text': 'The answer is scoped to readiness.'})

            engine = ThoughtEngine(store, model)
            engine.retrieve = lambda *args, **kwargs: [changed, overview]
            result = engine.consolidate([changed])[0]
            self.assertEqual(result.question, question)
            self.assertNotEqual(result.id, overview.id)
            self.assertEqual(store.versions(overview.id), [overview])
            self.assertIn((overview.id, overview.version),
                          [(basis['thought_id'], basis['version']) for basis in result.bases])
            self.assertEqual(calls, ['Find useful shared thought question', 'Construct thought', 'Verify thought'])

    def test_relevance_is_checked_again_if_a_candidate_changes_during_review(self):
        now = datetime.now(timezone.utc)
        with MemoryStore(':memory:') as memory:
            store, queue = ThoughtStore(memory), CaptureQueue(memory, clock=lambda: now)
            event = queue.capture_event('s', 'user', 'Swiping is allowed only in the deck.', source='fixture')
            source = store.publish(event.prompt, origin={'event_id': event.id, 'kind': 'source_interpretation'},
                                   bases=[{'event_id': event.id}])
            target = store.publish('Swiping is available.', question='When can I swipe?')
            reviewed = []

            def model(request):
                option = next(record for record in request.records if 'refresh_option' in record)
                reviewed.append(option['text'])
                if len(reviewed) == 1:
                    store.publish('Swiping and tapping are available.', question=target.question,
                                  thought_id=target.id, expected_version=target.version)
                return json.dumps({'question': None, 'text': 'The deck condition changes this answer.',
                                   'affected_candidates': [option['refresh_option']]})

            engine = ThoughtEngine(store, model)
            engine.retrieve = lambda *args, **kwargs: [source, store.get(target.id)]
            worker = ThoughtWorker(queue, engine, lane='maintenance')
            worker.enqueue_changed([source])
            self.assertEqual(worker.run_once()['status'], 'pending')
            self.assertIsNone(worker.work.get(target.id))
            now += timedelta(seconds=10)
            self.assertEqual(worker.run_once()['status'], 'completed')
            self.assertEqual(reviewed, [target.text, 'Swiping and tapping are available.'])
            pending = worker.work.get(target.id)
            self.assertEqual((pending['version'], pending['action']), (2, 'refresh'))

    def test_same_known_basis_and_old_unconfirmed_search_job_cost_no_model_calls(self):
        with MemoryStore(':memory:') as memory:
            store, queue = ThoughtStore(memory), CaptureQueue(memory)
            child = store.publish('Current child.')
            parent = store.publish('Current parent.',question='Current facts?',
                                   bases=[{'thought_id':child.id,'version':child.version}])
            unrelated = store.publish('Unrelated answer.',question='Unrelated question?')
            engine = ThoughtEngine(store,lambda _: self.fail('unconfirmed maintenance made a model call'))
            engine.retrieve = lambda *args,**kwargs: [unrelated]
            worker = ThoughtWorker(queue,engine,lane='maintenance')
            worker.enqueue_dependents([child])
            self.assertIsNone(worker.work.get(parent.id))
            self.assertIsNone(worker.work.get(unrelated.id))
            worker.work.enqueue_refresh(unrelated,(child.id,child.version))
            self.assertEqual(worker.run_once()['status'],'completed')

    def test_rephrasing_updates_ground_version_without_waking_its_parent(self):
        with MemoryStore(':memory:') as memory:
            store, queue = ThoughtStore(memory), CaptureQueue(memory)
            child = store.publish('Blue is supported.')
            parent = store.publish('Blue is supported.',question='Is blue supported?',
                                   metadata={'protocol':'unified-thought-v1','verification':'accepted'},
                                   bases=[{'thought_id':child.id,'version':child.version}])
            grandparent = store.publish('The blue option is available.',question='What color is available?',
                                        bases=[{'thought_id':parent.id,'version':parent.version}])
            child = store.publish('Blue is supported. Red is discontinued.',thought_id=child.id,
                                  expected_version=child.version)
            def model(request):
                if request.task == 'Construct thought':
                    return json.dumps({'thought':{'question':None,'text':'Blue is available.',
                                                   'support_fragments':[0]},'needs_detail':None})
                return json.dumps({'verdict':'accept','text':'Same answer and confidence.',
                                   'meaning_changed':False})
            engine = ThoughtEngine(store,model)
            worker = ThoughtWorker(queue,engine,lane='maintenance')
            worker.enqueue_dependents([child])
            latest_work = worker.run_once()
            self.assertEqual(latest_work['status'],'completed',latest_work)
            refreshed = store.get(parent.id)
            self.assertGreater(refreshed.version,parent.version)
            self.assertEqual(refreshed.text,parent.text)
            self.assertEqual(refreshed.bases[0]['version'],child.version)
            self.assertIsNone(worker.work.get(grandparent.id))
            self.assertTrue(engine._fresh(grandparent))

    def test_changed_meaning_queues_dependents_before_post_publication_failure(self):
        now = datetime.now(timezone.utc)
        with MemoryStore(':memory:') as memory:
            store, queue = ThoughtStore(memory), CaptureQueue(memory, clock=lambda: now)
            child = store.publish('Blue is supported.')
            parent = store.publish('Blue is supported.', question='Is blue supported?',
                                   metadata={'verification': 'accepted'},
                                   bases=[{'thought_id': child.id, 'version': child.version}])
            grandparent = store.publish('Blue is an available option.', question='What is available?',
                                        bases=[{'thought_id': parent.id, 'version': parent.version}])
            child = store.publish('Blue is discontinued.', thought_id=child.id, expected_version=child.version)
            calls = []

            def model(request):
                self.assertFalse(memory._writing)
                calls.append(request.task)
                if request.task == 'Construct thought':
                    return json.dumps({'thought': {'question': None, 'text': child.text,
                                                   'support_fragments': [0]}, 'needs_detail': None})
                return json.dumps({'verdict': 'accept', 'text': 'Blue is now discontinued.',
                                   'meaning_changed': True})

            def trace(event):
                if event['stage'] == 'publication':
                    raise RuntimeError('Interrupted after publishing the new parent version')

            engine = ThoughtEngine(store, model, trace=trace)
            worker = ThoughtWorker(queue, engine, lane='maintenance')
            worker.enqueue_dependents([child])
            self.assertEqual(worker.run_once()['status'], 'pending')
            new_parent = store.get(parent.id)
            self.assertEqual(new_parent.version, parent.version + 1)
            pending = worker.work.claim()
            self.assertIsNotNone(pending)
            self.assertEqual(pending['thought_id'], grandparent.id)
            self.assertEqual(worker.work.causes(pending), [['basis', parent.id, new_parent.version]])
            worker.work.finish(pending, grandparent.version)

            now += timedelta(seconds=10)
            restarted = ThoughtWorker(queue, ThoughtEngine(store, model), lane='maintenance')
            self.assertEqual(restarted.run_once()['status'], 'completed')
            self.assertIsNone(restarted.run_once())
            self.assertEqual(calls, ['Construct thought', 'Verify thought'])

    def test_automatic_overview_uses_the_prepared_short_title_not_its_body(self):
        with MemoryStore(':memory:') as memory:
            store = ThoughtStore(memory)
            source = store.publish('The deck uses a short swipe.',metadata={'verification':'accepted'})
            body = 'The deck uses a short swipe to accept a card. Other views have no swipe.'
            title = 'Accepting cards with a swipe'
            proposal = {'question':'How does a user accept deck cards?','text':title}
            def model(request):
                if request.task == 'Find useful shared thought question':
                    return json.dumps(proposal)
                if request.task == 'Construct thought':
                    return json.dumps({'thought':{'question':None,'text':body,
                                                   'support_fragments':[0]},'needs_detail':None})
                return json.dumps({'verdict':'accept','text':'The scope is the deck.'})
            engine = ThoughtEngine(store,model)
            engine.retrieve = lambda *args,**kwargs: [source]
            result = engine.consolidate([source],on_affected=lambda _:None)[0]
            self.assertEqual(result.metadata.get('title'),title)
            proposal['text'] = body * 2
            self.assertEqual(engine.consolidate([source],on_affected=lambda _:None),[])
