"""Explicit writer/reviewer dispatch and replay identity, without provider calls."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.model import ModelError,ModelRequest


class Backend:
    def __init__(self,*,model,**options):self.requested_model=model;self.calls=[];self.failure=None
    def __call__(self,request):
        self.calls.append(request)
        if self.failure:raise self.failure
        return self.requested_model


class ModelReviewRoutingTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=str(Path(self.temp.name)/'fixture.sqlite3')

    def test_canonical_and_legacy_reviews_route_to_reviewer_and_keep_the_request(self):
        config=ServiceConfig(self.db,'codex',model='writer-fixture',review_model='checker-fixture')
        with patch('prototypes.assertion_memory.mcp_server.CodexModel',side_effect=Backend):model,_,_=config.components(None)
        for task in ('Verify thought','Verify source thought batch','Check ready thought','Disambiguate thought support',
                     'Review memory view name','Review grounded memory overview','Verify contextual assertion interpretations'):
            request=ModelRequest(task,[])
            self.assertEqual(model(request),'checker-fixture')
            self.assertIs(model.model_for(task).calls[-1],request)
        for task in ('Construct thought','Find useful shared thought question','Navigate thought graph'):
            self.assertEqual(model(ModelRequest(task,[])),'writer-fixture')
        # Legacy overview.task is free-form user text; usage_task is the real job.
        self.assertEqual(model(ModelRequest('Verify thought',[],usage_task='Generate memory answer')),'writer-fixture')
        self.assertEqual(model(SimpleNamespace(task='Review memory view name')),'checker-fixture')

    def test_reviewer_failure_propagates_without_running_primary_as_fallback(self):
        config=ServiceConfig(self.db,'codex',model='writer-fixture',review_model='checker-fixture')
        with patch('prototypes.assertion_memory.mcp_server.CodexModel',side_effect=Backend):model,_,_=config.components(None)
        review=model.model_for('Verify thought');primary=model.model_for('Construct thought')
        review.failure=ModelError('review unavailable')
        with self.assertRaisesRegex(ModelError,'review unavailable'):model(ModelRequest('Verify thought',[]))
        self.assertEqual(primary.calls,[])
        self.assertEqual(review.requested_model,'checker-fixture')

    def test_requested_effort_is_explicit_for_both_codex_commands_and_profile(self):
        config=ServiceConfig(self.db,'codex',model='writer-fixture',review_model='checker-fixture',
                             reasoning_effort='low')
        routed,_,_=config.components(None)
        for task in ('Construct thought','Verify thought'):
            chosen=routed.model_for(task)
            self.assertEqual(chosen.reasoning_effort,'low')
            self.assertIn('model_reasoning_effort="low"',chosen.command)
        restored=ServiceConfig(**json.loads(json.dumps(asdict(config))))
        self.assertEqual(restored.reasoning_effort,'low');self.assertEqual(restored.route_profile_key,config.route_profile_key)
        default=ServiceConfig(self.db,'codex',model='writer-fixture',review_model='checker-fixture')
        self.assertNotEqual(default.route_profile_key,config.route_profile_key)
        primary,_,_=ServiceConfig(self.db,'codex',model='writer-fixture').components(None)
        self.assertIsNone(primary.reasoning_effort)
        self.assertFalse(any('model_reasoning_effort=' in part for part in primary.command))
        for provider,options in (('none',{}),('subprocess',{'command':('fixture',)}),
                ('local',{'endpoint':'http://127.0.0.1:1','model':'fixture'})):
            with self.assertRaises(ValueError):ServiceConfig(self.db,provider,reasoning_effort='low',**options)

    def test_roundtrip_profile_and_pipeline_key_bind_both_explicit_models(self):
        base=ServiceConfig(self.db,'codex',model='writer-fixture',review_model='checker-fixture',
            processing_session_ids=('selected',),timeout=240)
        restored=ServiceConfig(**json.loads(json.dumps(asdict(base))))
        self.assertEqual(restored.model,base.model);self.assertEqual(restored.review_model,base.review_model)
        self.assertEqual(restored.processing_session_ids,base.processing_session_ids);self.assertIsNone(restored.timeout)
        with patch('prototypes.assertion_memory.mcp_server.CodexModel',side_effect=Backend):
            model,_,key=restored.components(None)
            changed=ServiceConfig(self.db,'codex',model='writer-fixture',review_model='other-checker')
            other,_,other_key=changed.components(None)
            self.assertNotEqual(key,other_key);self.assertNotEqual(model.route_profile_key,other.route_profile_key)
            self.assertEqual(model.route_profile_key,restored.route_profile_key)
            single,_,single_key=ServiceConfig(self.db,'codex',model='writer-fixture').components(None)
            self.assertIsInstance(single,Backend)
            self.assertNotEqual(single.route_profile_key,model.route_profile_key)
        class LocalBackend:
            def __init__(self,endpoint,model,**options):self.model=model
            def __call__(self,request):return self.model
        local=ServiceConfig(self.db,'local',endpoint='http://127.0.0.1:1',
                            model='local-writer',review_model='local-checker')
        with patch('prototypes.assertion_memory.mcp_server.LocalOpenAIModel',side_effect=LocalBackend):
            routed,_,_=local.components(None)
            self.assertEqual(routed.model_for('Verify thought').requested_model,'local-checker')
        one=ServiceConfig(self.db,'subprocess',command=('command-one',))
        two=ServiceConfig(self.db,'subprocess',command=('command-two',))
        self.assertNotEqual(one.route_profile_key,two.route_profile_key)
        for review in ('',42):
            with self.assertRaises(ValueError):ServiceConfig(self.db,'codex',model='writer-fixture',review_model=review)
        for provider,options in (('none',{}),('subprocess',{'command':('fixture',)})):
            with self.assertRaises(ValueError):ServiceConfig(self.db,provider,review_model='checker-fixture',**options)

    def test_independent_review_effort_survives_worker_roundtrip_and_routes_commands(self):
        config=ServiceConfig(self.db,'codex',model='gpt-6.2-sol',review_model='gpt-6.2-sol',
                             reasoning_effort='medium',review_reasoning_effort='low')
        restored=ServiceConfig(**json.loads(json.dumps(asdict(config))))
        routed,_,key=restored.components(None)
        for task,effort in (('Construct thought','medium'),('Verify thought','low')):
            selected=routed.model_for(task)
            self.assertEqual(selected.reasoning_effort,effort)
            self.assertIn('model_reasoning_effort='+json.dumps(effort),selected.command)
        inherited=ServiceConfig(self.db,'codex',model='gpt-6.2-sol',review_model='gpt-6.2-sol',
                                reasoning_effort='medium')
        self.assertNotEqual(config.route_profile_key,inherited.route_profile_key)
        self.assertNotEqual(key,inherited.components(None)[2])
        same=ServiceConfig(self.db,'codex',model='gpt-6.2-sol',review_model='gpt-6.2-sol',
                           reasoning_effort='medium',review_reasoning_effort='medium')
        self.assertEqual(same.route_profile_key,inherited.route_profile_key)

    def test_review_effort_requires_codex_and_an_explicit_reviewer(self):
        for effort in ('',42,'   '):
            with self.subTest(effort=effort),self.assertRaises(ValueError):
                ServiceConfig(self.db,'codex',model='writer',review_model='checker',
                              review_reasoning_effort=effort)
        with self.assertRaises(ValueError):
            ServiceConfig(self.db,'codex',model='writer',review_reasoning_effort='low')
        with self.assertRaises(ValueError):
            ServiceConfig(self.db,'local',model='writer',review_model='checker',
                          endpoint='http://127.0.0.1:1',review_reasoning_effort='low')

    def test_cli_preserves_separate_review_effort(self):
        from prototypes.assertion_memory.mcp_server import main
        with patch('prototypes.assertion_memory.mcp_server.create_server') as create:
            self.assertEqual(main(['--db',self.db,'--provider','codex','--model','writer',
                '--review-model','checker','--reasoning-effort','medium',
                '--review-reasoning-effort','low','--no-background']),0)
        config=create.call_args.args[0]
        self.assertEqual(config.reasoning_effort,'medium')
        self.assertEqual(config.review_reasoning_effort,'low')

    def test_rebuild_identity_preserves_legacy_defaults_and_binds_effective_override(self):
        from prototypes.assertion_memory.rebuild import _provider_identity,_json,_sha
        options={'provider':'codex','model':'writer','review_model':'checker','reasoning_effort':'medium'}
        settings={'db':self.db,'worker_service':options}
        from prototypes.assertion_memory.embeddings import MULTILINGUAL_MODEL
        legacy = dict(options, command=(), endpoint=None, timeout=None,
                      embedding_mode='lexical', embedding_model=MULTILINGUAL_MODEL,
                      embedding_cache=None, source_workers=2, processing_session_ids=None)
        expected=_sha(_json(legacy).encode())
        self.assertEqual(_provider_identity(settings),expected)
        same={'db':self.db,'worker_service':dict(options,review_reasoning_effort='medium')}
        self.assertEqual(_provider_identity(same),expected)
        separate={'db':self.db,'worker_service':dict(options,review_reasoning_effort='low')}
        self.assertNotEqual(_provider_identity(separate),expected)
        selected = {'db': self.db, 'worker_service': dict(
            options, batch_source_construction=True, source_event_ids=('selected-event',))}
        self.assertEqual(_provider_identity(selected), expected)


if __name__=='__main__':unittest.main()
