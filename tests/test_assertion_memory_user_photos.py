"""User JPEG/PNG protocol structure, synthetic caption/path/bytes only."""
import base64
import json
import unittest
from . import test_assertion_memory_capture as fixture
from .test_assertion_memory_artifacts import PNG,AID,ArtifactRemote


class UserPhotoTest(unittest.TestCase):
    setUp=fixture.CaptureTest.setUp
    append=fixture.CaptureTest.append
    turn=fixture.CaptureTest.turn
    assistant=fixture.CaptureTest.assistant
    pair=fixture.CaptureTest.pair
    poll=fixture.CaptureTest.poll
    messages=fixture.CaptureTest.messages

    def photo(self,*,trigger='remote_ios',thread='physical',path='/synthetic/private-photo.jpg',caption='Actual typed words.\nKeep this exact condition.',parent=None,completion=True):
        self.append({'type':'event_msg','payload':{'type':'task_started','turn_id':'photo-turn','root_turn_id':'photo-turn',
            'turn_attribution':{'turn_trigger':trigger,'parent_turn_id':parent,'initiating_agent_path':None,'root_turn_id':'photo-turn'}}})
        parts=[{'type':'input_text','text':caption},{'type':'input_text','text':'<image image_index=0 path="'+path+'">'},
            {'type':'input_image','detail':'original','image_url':'data:image/png;base64,'+base64.b64encode(PNG).decode()},
            {'type':'input_text','text':'</image>'}]
        kinds=['user.text','user.text','user.image','user.text']
        self.append({'timestamp':'2026-11-16T08:00:00Z','type':'response_item','payload':{'type':'message','id':'photo-R','role':'user','content':parts,
            'internal_chat_message_metadata_passthrough':{'turn_id':'photo-turn','content_item_kinds':kinds,
                'content_item_metadata':[{'provenance':{'type':'user'}} for _ in parts]}}})
        if completion:self.complete(thread=thread,path=path,caption=caption)
        return caption

    def complete(self,*,thread='physical',path='/synthetic/private-photo.jpg',caption='Actual typed words.\nKeep this exact condition.'):
        self.append({'timestamp':'2026-11-16T09:00:00Z','type':'event_msg','payload':{'type':'item_completed','thread_id':thread,'turn_id':'photo-turn',
            'item':{'type':'UserMessage','id':'photo-I','client_id':'synthetic-client',
                'content':[{'type':'text','text':caption,'text_elements':[]},{'type':'local_image','path':path}]}}})

    def test_actual_user_caption_and_photo_retained_with_R_clock_and_generated_manifest(self):
        caption=self.photo();self.assistant();self.capture.register(self.store,self.raw);result=self.poll()
        self.assertEqual(result['gaps'],[]);self.assertEqual(len(self.messages()),2)
        args=self.messages()[0]['arguments'];self.assertEqual(args['role'],'user');self.assertEqual(args['artifact_ids'],[AID])
        self.assertTrue(args['text'].startswith(caption));self.assertIn('not user words',args['text'])
        self.assertNotIn('/synthetic/private-photo.jpg',args['text']);self.assertNotIn('<image',args['text'])
        self.assertNotIn(base64.b64encode(PNG).decode(),args['text']);self.assertIn('not_interpreted',args['text'])
        self.assertEqual(args['source_message_at'],'2026-11-16T08:00:00Z')
        self.assertEqual(bytes(self.store.db.execute('SELECT data FROM artifacts').fetchone()[0]),PNG)
        from synthetic_memory_adapter.frontier import Coordinator
        remote=ArtifactRemote();c=Coordinator(self.store,remote);c.observe('physical',result);c.tick()
        self.assertEqual(self.messages()[0]['status'],'acknowledged');self.assertEqual(remote.blobs[AID],PNG)
        names=[name for name,_ in remote.calls];self.assertLess(names.index('memory_store_artifact'),names.index('memory_capture_message'))

    def test_photo_native_client_requires_matching_current_thread_I_and_path_proof(self):
        self.photo(trigger=None,completion=False);self.capture.register(self.store,self.raw);self.poll()
        self.assertEqual(self.messages(),[])
        self.complete(thread='another-thread');self.poll();self.assertEqual(self.messages(),[])
        # Wrong-thread completion cannot publish a caller-declared photo as current.
        self.assertFalse(any(row[0] for row in self.store.db.execute('SELECT receipt FROM artifacts')))

    def test_photo_without_I_is_pending_and_then_visible_limit_not_human_R_fallback(self):
        self.photo(completion=False);self.capture.register(self.store,self.raw);self.poll()
        self.assertEqual(self.messages(),[]);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],1)
        self.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':'photo-turn'}})
        result=self.poll();self.assertEqual(self.messages(),[])
        self.assertIn('user_photo_occurrence_not_proven',result['gaps'])

    def test_goal_parent_photo_origin_is_not_weakened_by_image_support(self):
        self.photo(trigger='goal',parent='parent-turn');self.capture.register(self.store,self.raw);self.poll()
        self.assertEqual(self.messages(),[]);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],0)

    def test_path_mismatch_does_not_fetch_file_or_publish_user_words(self):
        self.photo(completion=False);self.complete(path='/another/private-image.jpg')
        self.capture.register(self.store,self.raw);result=self.poll();self.assertEqual(self.messages(),[])
        self.assertIn('unsupported_user_photo_proof',result['gaps'])

    def test_native_client_photo_positive_proof_and_parser_state_never_embeds_base64(self):
        self.photo(trigger=None,completion=False);self.capture.register(self.store,self.raw);self.poll()
        self.assertNotIn(base64.b64encode(PNG).decode(),json.dumps(self.store.session('physical')))
        self.complete();self.poll();self.assertEqual(self.messages(),[])
        self.assistant();result=self.poll()
        self.assertEqual(result['gaps'],[]);self.assertEqual(len(self.messages()),2)
        self.assertEqual(self.messages()[0]['arguments']['artifact_ids'],[AID])

    def test_unknown_image_format_preserves_verified_words_with_explicit_limit(self):
        caption=self.photo()
        rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        rows[-2]['payload']['content'][2]['image_url']='https://example.invalid/photo?token=DO-NOT-RETAIN'
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.assistant();self.capture.register(self.store,self.raw);result=self.poll()
        args=self.messages()[0]['arguments'];self.assertTrue(args['text'].startswith(caption))
        self.assertNotIn('artifact_ids',args);self.assertNotIn('DO-NOT-RETAIN',args['text'])
        self.assertIn('unsupported_user_photo_media:external_image_not_fetched',result['gaps'])

    def test_photo_replay_is_idempotent_and_scalar_callbacks_unchanged(self):
        self.photo();self.assistant();self.capture.register(self.store,self.raw);result=self.poll()
        from synthetic_memory_adapter.frontier import Coordinator
        remote=ArtifactRemote();c=Coordinator(self.store,remote);c.observe('physical',result);c.tick()
        before=self.messages()[0]
        from synthetic_memory_adapter.repair import reset_parser_checkpoint,REASON
        reset_parser_checkpoint(self.store,'physical',expected_header_sha256=self.store.session('physical')['header_hash'],reason=REASON)
        self.poll();after=self.messages()[0]
        self.assertEqual(before['arguments'],after['arguments']);self.assertEqual(before['receipt'],after['receipt'])
        self.assertEqual(after['status'],'acknowledged');self.assertEqual(len(self.messages()),2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],1)

    def test_supported_jpeg_bytes_are_retained_without_any_image_file_access(self):
        data=b'\xff\xd8\xff\xe0synthetic-jpeg-signature-only\xff\xd9'
        self.photo();rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        rows[-2]['payload']['content'][2]['image_url']='data:image/jpeg;base64,'+base64.b64encode(data).decode()
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.assistant();self.capture.register(self.store,self.raw);self.poll()
        blob=self.store.db.execute('SELECT data,mime_type FROM artifacts').fetchone()
        self.assertEqual(bytes(blob['data']),data);self.assertEqual(blob['mime_type'],'image/jpeg')
        self.assertEqual(self.messages()[0]['arguments']['role'],'user')

    def test_large_user_photo_stages_blob_before_cursor_and_keeps_original_text(self):
        data=b'\x89PNG\r\n\x1a\n'+b'x'*(2*1024*1024-8)
        caption=self.photo();rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        rows[-2]['payload']['content'][2]['image_url']='data:image/png;base64,'+base64.b64encode(data).decode()
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.capture.register(self.store,self.raw);first=self.poll()
        self.assertTrue(first['backlog']);self.assertEqual(self.messages(),[])
        self.assertEqual(bytes(self.store.db.execute('SELECT data FROM artifacts').fetchone()[0]),data)
        self.assertNotIn(base64.b64encode(data).decode(),json.dumps(self.store.session('physical')))
        self.assistant();second=self.poll();self.assertEqual(second['gaps'],[])
        self.assertTrue(self.messages()[0]['arguments']['text'].startswith(caption))
