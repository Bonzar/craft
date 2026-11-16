"""Optional native images: synthetic bytes only, no vision/network/personal media."""
import base64
import hashlib
import json
import unittest
from . import test_assertion_memory_capture as fixture
from .test_assertion_memory_frontier import FakeRemote

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2f8AAAAASUVORK5CYII=')
SHA=hashlib.sha256(PNG).hexdigest();AID='artifact-'+SHA


class ArtifactRemote(FakeRemote):
    def __init__(self):super().__init__();self.blobs={};self.fail_upload=False;self.wrong_upload=False
    def call(self,name,args):
        if name=='memory_store_artifact':
            self.calls.append((name,dict(args)));data=base64.b64decode(args['data_base64'],validate=True)
            digest=hashlib.sha256(data).hexdigest();self.blobs['artifact-'+digest]=data
            if self.fail_upload:self.fail_upload=False;raise OSError('synthetic lost upload ACK')
            return {'artifact_id':'artifact-'+digest,'sha256':digest,'size_bytes':len(data),
                'mime_type':'image/png','not_interpreted':True} if not self.wrong_upload else {'artifact_id':'wrong'}
        if name=='memory_capture_message':
            if any(aid not in self.blobs for aid in args.get('artifact_ids',[])):raise ValueError('missing remote blob')
        return super().call(name,args)


class ArtifactTest(unittest.TestCase):
    setUp=fixture.CaptureTest.setUp
    append=fixture.CaptureTest.append
    turn=fixture.CaptureTest.turn
    pair=fixture.CaptureTest.pair
    poll=fixture.CaptureTest.poll
    messages=fixture.CaptureTest.messages
    def mixed(self,uri=None):
        self.turn();self.append({'type':'response_item','payload':{'type':'custom_tool_call','name':'synthetic-tool','call_id':'photo'}})
        output=[{'type':'input_text','text':'All business words and exceptions remain exactly.'},
                {'type':'input_text','text':json.dumps({'condition':'only after review, except unfinished cases','source_repository_credential':'SYNTHETIC_CREDENTIAL_VALUE'})},
                {'type':'input_image','detail':'high','image_url':uri or 'data:image/png;base64,'+base64.b64encode(PNG).decode()}]
        self.append({'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'photo','output':output}})
        self.capture.register(self.store,self.raw);return self.poll()
    def coordinator(self,remote):
        from synthetic_memory_adapter.frontier import Coordinator
        c=Coordinator(self.store,remote,clock=lambda:1000.0);return c

    def test_mixed_data_uri_keeps_text_and_binary_bytes_out_of_canonical_text(self):
        result=self.mixed();messages=self.messages();self.assertEqual([m['arguments']['role'] for m in messages],['user','tool'])
        args=messages[1]['arguments'];self.assertEqual(args['artifact_ids'],[AID]);projection=json.loads(args['text'])
        self.assertEqual(projection['output'][0]['text'],'All business words and exceptions remain exactly.')
        self.assertIn('only after review, except unfinished cases',projection['output'][1]['text'])
        self.assertNotIn('SYNTHETIC_CREDENTIAL_VALUE',args['text']);self.assertNotIn(base64.b64encode(PNG).decode(),args['text'])
        self.assertEqual(projection['artifact_refs'][0],{'artifact_id':AID,'sha256':SHA,'mime_type':'image/png','size_bytes':len(PNG),'not_interpreted':True})
        blob=self.store.db.execute('SELECT data FROM artifacts WHERE artifact_id=?',(AID,)).fetchone()[0]
        self.assertEqual(bytes(blob),PNG);self.assertEqual(result['gaps'],[])

    def test_upload_precedes_dependent_capture_and_exact_lost_ack_replays(self):
        result=self.mixed();remote=ArtifactRemote();remote.fail_upload=True;c=self.coordinator(remote);c.observe('physical',result);c.tick()
        tool=self.messages()[1];self.assertEqual(tool['status'],'pending')
        self.assertFalse(any(name=='memory_capture_message' and args.get('artifact_ids') for name,args in remote.calls))
        c.clock=lambda:1100.0;c.tick();uploads=[args for name,args in remote.calls if name=='memory_store_artifact']
        self.assertEqual(uploads[0],uploads[1]);self.assertEqual(remote.blobs[AID],PNG)
        self.assertEqual(self.messages()[1]['status'],'acknowledged')
        names=[name for name,args in remote.calls if name=='memory_store_artifact' or name=='memory_capture_message' and args.get('artifact_ids')]
        self.assertEqual(names,['memory_store_artifact','memory_store_artifact','memory_capture_message'])

    def test_missing_local_blob_or_wrong_upload_receipt_never_acknowledges_message(self):
        result=self.mixed();remote=ArtifactRemote();c=self.coordinator(remote);c.observe('physical',result)
        self.store.db.execute('DELETE FROM artifacts WHERE artifact_id=?',(AID,));self.store.db.commit();c.tick()
        self.assertEqual(self.messages()[1]['status'],'pending')
        self.assertFalse(any(name=='memory_capture_message' and args.get('artifact_ids') for name,args in remote.calls))

    def test_unsupported_external_image_preserves_all_text_and_exposes_limit(self):
        result=self.mixed('https://external.example.invalid/private-image?credential=DO-NOT-STORE')
        args=self.messages()[1]['arguments'];self.assertNotIn('artifact_ids',args)
        self.assertIn('All business words and exceptions remain exactly.',args['text'])
        self.assertNotIn('DO-NOT-STORE',args['text']);self.assertIn('not_interpreted',args['text'])
        self.assertIn('unsupported_tool_media:external_image_not_fetched',result['gaps'])

    def test_replay_preserves_same_attachment_refs_and_durable_blob_without_duplicate(self):
        self.mixed();before=[m['arguments'] for m in self.messages()]
        from synthetic_memory_adapter.repair import reset_parser_checkpoint,REASON
        reset_parser_checkpoint(self.store,'physical',expected_header_sha256=self.store.session('physical')['header_hash'],reason=REASON)
        self.poll();self.assertEqual(before,[m['arguments'] for m in self.messages()])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],1)

    def test_legacy_scalar_and_text_projection_arguments_remain_byte_identical(self):
        from synthetic_memory_adapter.tool_projection import project_result
        scalar='Exact old scalar observation, with exception.'
        self.assertEqual(project_result(scalar)['text'],scalar)
        value=[{'type':'input_text','text':json.dumps({'type':'image','url':'https://business.example.invalid/photo','condition':'keep this whole condition'})}]
        expected=json.dumps({'format':'native-tool-output/v1','output':value,'credential_redactions':0},ensure_ascii=False,sort_keys=True,separators=(',',':'))
        result=project_result(value);self.assertEqual(result['text'],expected);self.assertEqual(result['artifacts'],[])

    def test_wrong_upload_receipt_keeps_dependent_message_pending(self):
        result=self.mixed();remote=ArtifactRemote();remote.wrong_upload=True;c=self.coordinator(remote);c.observe('physical',result);c.tick()
        self.assertEqual(self.messages()[1]['status'],'pending')
        self.assertIsNone(self.store.db.execute('SELECT receipt FROM artifacts WHERE artifact_id=?',(AID,)).fetchone()[0])
        self.assertFalse(any(name=='memory_capture_message' and args.get('artifact_ids') for name,args in remote.calls))

    def test_image_size_limit_and_large_media_frame_do_not_widen_plain_text_limit(self):
        from synthetic_memory_adapter.artifacts import MAX_BYTES
        data=b'\x89PNG\r\n\x1a\n'+b'x'*(MAX_BYTES-8)
        result=self.mixed('data:image/png;base64,'+base64.b64encode(data).decode())
        self.assertEqual(result['gaps'],[])
        args=self.messages()[1]['arguments'];self.assertEqual(args['artifact_ids'],['artifact-'+hashlib.sha256(data).hexdigest()])
        row=self.store.db.execute('SELECT data FROM artifacts WHERE artifact_id=?',(args['artifact_ids'][0],)).fetchone()
        self.assertEqual(bytes(row[0]),data)

    def test_artifact_and_message_cursor_rollback_together_on_failed_capture_enqueue(self):
        from unittest.mock import patch
        with patch.object(self.store,'enqueue',side_effect=ValueError('synthetic rollback')):
            with self.assertRaises(ValueError):self.mixed()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],0)
        self.assertEqual(self.store.session('physical')['cursor'],0)

    def test_future_resume_partial_record_is_discarded_before_size_limit_with_proven_cutoff(self):
        self.path.write_bytes(self.path.read_bytes()+b'old partial record')
        raw={**self.raw,'source':'resume'};self.capture.register(self.store,raw)
        attached=self.store.session('physical')['attachment_start_offset']
        with self.path.open('ab') as f:f.write(b'x'*(self.capture.MAX_RECORD+5)+b'\n')
        self.turn();result=self.poll()
        self.assertEqual(result['gaps'],[]);self.assertEqual(len(self.messages()),1)
        state=self.store.session('physical');self.assertEqual(state['attachment_start_offset'],attached)
        self.assertEqual(state['exclusions']['pre_attachment_partial_record'],1)

    def test_image_aliases_dedup_and_unsupported_nontext_keep_explicit_limits(self):
        from synthetic_memory_adapter.tool_projection import project_result
        uri='data:image/x-png;base64,'+base64.b64encode(PNG).decode()
        output=[{'type':'input_text','text':'Keep every word.'},
                {'type':'input_image','image_url':uri},{'type':'input_image','image_url':uri},
                {'type':'input_audio','data':'DO-NOT-PUT-AUDIO-IN-TEXT'}]
        result=project_result(output)
        self.assertEqual(len(result['artifacts']),1);self.assertEqual(result['artifacts'][0]['metadata']['artifact_id'],AID)
        self.assertEqual(result['artifacts'][0]['metadata']['mime_type'],'image/png')
        self.assertNotIn('DO-NOT-PUT-AUDIO-IN-TEXT',result['text'])
        self.assertIn('unsupported_nontext_type',result['omissions']);self.assertIn('Keep every word.',result['text'])

    def test_future_nonmedia_oversize_records_keep_bounded_gap_witness(self):
        self.turn();self.append({'type':'response_item','payload':{'type':'function_call_output','output':'z'*(self.capture.MAX_RECORD+1)}})
        self.capture.register(self.store,self.raw);self.poll();state=self.store.session('physical')
        witness=state['gap_witnesses'][-1]
        self.assertEqual(witness['reason'],'oversized_record')
        self.assertGreaterEqual(witness['start'],state['attachment_start_offset'])
        with self.path.open('rb') as f:f.seek(witness['start']);data=f.read(witness['end']-witness['start'])
        self.assertEqual(hashlib.sha256(data).hexdigest(),witness['sha256'])
