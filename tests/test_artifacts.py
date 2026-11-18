"""Synthetic image bytes only; artifact storage never interprets the depicted world."""
import asyncio
import base64
import hashlib
import json
import sqlite3
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.mcp_server import ServiceConfig,create_server
from prototypes.assertion_memory.backup import snapshot_database

def png():
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x00\x80\x00\xff'))+chunk(b'IEND',b'')

class ArtifactTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'memory.sqlite3';self.store=MemoryStore(str(self.path));self.addCleanup(self.store.close)
        self.service=MemoryService(self.store);self.binary=png();self.encoded=base64.b64encode(self.binary).decode()
    def upload(self,**extra):return self.service.store_artifact(self.encoded,'image/png',**extra)
    def capture(self,**extra):return self.service.capture_message('session','m','tool:fixture','tool','Synthetic tool text.',None,'fixture://tool',**extra)
    def test_idempotent_content_and_readonly_metadata_or_exact_bytes(self):
        receipt=self.upload();self.assertEqual(receipt['artifact_id'],'artifact-'+hashlib.sha256(self.binary).hexdigest())
        self.assertEqual(receipt,self.service.store_artifact(self.encoded,'image/x-png'))
        self.assertTrue(receipt['not_interpreted']);self.assertEqual(receipt['size_bytes'],len(self.binary))
        before=self.store._connection.total_changes;self.store._connection.execute('PRAGMA query_only=ON')
        self.assertEqual(self.service.artifact(receipt['artifact_id']),receipt)
        self.assertNotIn('data_base64',receipt)
        content=self.service.artifact(receipt['artifact_id'],include_content=True)
        self.assertEqual(base64.b64decode(content['data_base64']),self.binary)
        self.assertEqual(before,self.store._connection.total_changes)
    def test_strict_size_base64_mime_magic_expected_hash_validation(self):
        for encoded,mime,sha in [(self.encoded+'\n','image/png',None),('data:image/png;base64,'+self.encoded,'image/png',None),(self.encoded,'image/jpeg',None),(base64.b64encode(b'not an image').decode(),'image/png',None),(self.encoded,'application/octet-stream',None),(self.encoded,'image/png','0'*64),(self.encoded,'image/png','bad')]:
            with self.subTest(mime=mime,sha=sha),self.assertRaises(ValueError):self.service.store_artifact(encoded,mime,sha256=sha)
        oversized=base64.b64encode(self.binary+b'0'*(2*1024*1024)).decode()
        with self.assertRaises(ValueError):self.service.store_artifact(oversized,'image/png')
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_artifacts').fetchone()[0],0)
    def test_capture_refs_exact_replay_changed_or_missing_refs_atomic(self):
        one=self.upload();two=self.service.store_artifact(base64.b64encode(self.binary+b'other').decode(),'image/png')
        first=self.capture(artifact_ids=[one['artifact_id']],source_message_at='2026-01-01T00:00:00Z')
        repeat=self.capture(artifact_ids=[one['artifact_id']],source_message_at='2026-01-01T00:00:00Z')
        self.assertEqual(first['event']['id'],repeat['event']['id'])
        raw=self.service.queue.get_event(first['event']['id'])
        self.assertEqual(raw.metadata['artifact_refs'],[one]);self.assertNotIn(self.encoded,json.dumps(raw.metadata))
        for refs in (None,[],[two['artifact_id']]):
            with self.assertRaises(ValueError):self.capture(artifact_ids=refs)
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
    def test_missing_artifact_rejects_raw_job_receipt_and_delivery_together(self):
        state=self.service.ingest_register('producer','session')
        self.service.ingest_report(state['stream_id'],state['epoch'],1,1,'catching_up',0)
        with self.assertRaises(ValueError):self.capture(artifact_ids=['artifact-'+'0'*64],delivery={'stream_id':state['stream_id'],'epoch':state['epoch'],'seq':1})
        for table in ('memory_raw_events','memory_capture_jobs','memory_message_receipts','memory_ingest_receipts'):
            self.assertEqual(self.store._connection.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_old_text_only_replay_unchanged_and_attachment_bound(self):
        first=self.capture();payload=self.store._connection.execute('SELECT payload FROM memory_message_receipts').fetchone()[0]
        self.assertEqual(first['event']['id'],self.capture(artifact_ids=[])['event']['id'])
        self.assertEqual(payload,self.store._connection.execute('SELECT payload FROM memory_message_receipts').fetchone()[0])
        one=self.upload()
        with self.assertRaises(ValueError):self.capture(artifact_ids=[one['artifact_id']])
        with self.assertRaises(ValueError):self.capture(artifact_ids=[one['artifact_id']]*9)
    def test_exact_decoded_limit_and_same_stream_lost_ack_replay(self):
        padding=b'fixture\x00'+b'0'*(2*1024*1024-len(self.binary)-12-8)
        ancillary=struct.pack('>I',len(padding))+b'tEXt'+padding+struct.pack('>I',zlib.crc32(b'tEXt'+padding)&0xffffffff)
        bounded=self.binary[:-12]+ancillary+self.binary[-12:]
        self.assertEqual(len(bounded),2*1024*1024)
        receipt=self.service.store_artifact(base64.b64encode(bounded).decode(),'image/png')
        self.assertEqual(receipt['size_bytes'],2*1024*1024)
        stream=self.service.ingest_register('producer','session')
        self.service.ingest_report(stream['stream_id'],stream['epoch'],1,1,'catching_up',0)
        delivery={'stream_id':stream['stream_id'],'epoch':stream['epoch'],'seq':1}
        first=self.capture(artifact_ids=[receipt['artifact_id']],delivery=delivery)
        retry=self.capture(artifact_ids=[receipt['artifact_id'],receipt['artifact_id']],delivery=delivery)
        self.assertEqual(first['event']['id'],retry['event']['id'])
        self.assertEqual(retry['delivery']['acked_through'],1)
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_ingest_receipts').fetchone()[0],1)
    def test_artifact_operations_do_not_change_source_version_or_pipeline(self):
        version=self.store.version;pipeline=self.service.engine.pipeline_key
        receipt=self.upload();self.service.artifact(receipt['artifact_id'],include_content=True)
        self.assertEqual(self.store.version,version);self.assertEqual(self.service.engine.pipeline_key,pipeline)
    def test_artifact_immutability_and_sqlite_backup_restore(self):
        one=self.upload()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction(write=True) as c:c.execute("UPDATE memory_artifacts SET mime_type='image/gif'")
        backup=Path(self.tmp.name)/'backup.sqlite3';restored=Path(self.tmp.name)/'restored.sqlite3'
        snapshot_database(self.path,backup);snapshot_database(backup,restored)
        with MemoryStore(str(restored)) as store:
            content=MemoryService(store).artifact(one['artifact_id'],include_content=True)
            self.assertEqual(base64.b64decode(content['data_base64']),self.binary)
    def test_model_boundary_gets_only_technical_refs_and_no_store_model_calls(self):
        from prototypes.assertion_memory.model import ModelRequest,format_prompt
        with patch.object(self.service.engine,'model',side_effect=AssertionError('no model work')):
            receipt=self.upload();capture=self.capture(artifact_ids=[receipt['artifact_id']])
            raw=self.service.queue.get_event(capture['event']['id'])
            prompt=format_prompt(ModelRequest('Synthetic boundary',[{'text':raw.prompt,'metadata':raw.metadata}]))
        self.assertNotIn(self.encoded,prompt);self.assertNotIn('data:image',prompt)
        self.assertIn(receipt['artifact_id'],prompt);self.assertIn('not_interpreted',prompt)

class ArtifactSDKTest(unittest.TestCase):
    setUp=ArtifactTest.setUp
    upload=ArtifactTest.upload
    def test_real_sdk_and_authenticated_http_return_typed_image_only_on_demand(self):
        import httpx
        from prototypes.assertion_memory.http_server import create_http_app
        from mcp.types import CallToolResult
        async def scenario():
            config=ServiceConfig(str(self.path),'none');server=create_server(config,background=False,store=self.store)
            listed={tool.name:tool for tool in await server.list_tools()}
            self.assertIn('memory_store_artifact',list(listed));self.assertIn('memory_artifact',list(listed))
            receipt=self.upload()
            result=await server.call_tool('memory_artifact',{'artifact_id':receipt['artifact_id'],'include_content':True})
            if not isinstance(result,CallToolResult):
                self.fail('Expected direct CallToolResult for typed image return')
            self.assertEqual(result.structuredContent,receipt)
            image=next(part for part in result.content if part.type=='image')
            self.assertEqual(base64.b64decode(image.data),self.binary);self.assertEqual(image.mimeType,'image/png')
            token='synthetic-artifact-wire-'+'x'*40;app=create_http_app(config,token,'artifact.test:9443',background=False)
            async with app.lifespan(),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://artifact.test:9443') as client:
                body={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'memory_artifact','arguments':{'artifact_id':receipt['artifact_id'],'include_content':True}}}
                self.assertEqual((await client.post('/mcp',json=body,headers={'Accept':'application/json, text/event-stream'})).status_code,401)
                response=await client.post('/mcp',json=body,headers={'Accept':'application/json, text/event-stream','Authorization':'Bearer '+token})
                self.assertEqual(response.status_code,200)
                value=response.json()['result'];self.assertEqual(value['structuredContent'],receipt)
                self.assertTrue(any(part['type']=='image' for part in value['content']))
                body['params']['arguments']['include_content']=False
                meta=(await client.post('/mcp',json=body,headers={'Accept':'application/json, text/event-stream','Authorization':'Bearer '+token})).json()['result']
                self.assertFalse(any(part['type']=='image' for part in meta['content']))
                self.assertNotIn('data_base64',json.dumps(meta))
        asyncio.run(scenario())

if __name__=='__main__':unittest.main()
