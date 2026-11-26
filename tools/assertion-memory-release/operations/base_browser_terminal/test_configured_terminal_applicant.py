import hashlib
import io
import tarfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from configured_base_prod_gate import BASE,Original,Content,Promotion,Policy,permit
from configured_terminal_applicant import EXPECTED,MARKER,MANIFEST,archive_contract,operate_terminal

def fixture(items):
    b=io.BytesIO()
    with tarfile.open(fileobj=b,mode='w:gz') as t:
        for name,data in items:
            info=tarfile.TarInfo(name);info.size=len(data);t.addfile(info,io.BytesIO(data))
    return b.getvalue()
def promotion(archive):
    digest=hashlib.sha256(archive).hexdigest()
    return Promotion(BASE,Original('assertion-memory-v0.1.40','a'*40,'2','3','4',digest),
                    'c'*40,'5','6',Content(digest),'workflow_run')
marker_line=hashlib.sha256(EXPECTED).hexdigest().encode()+b'  ./'+MARKER.encode()+b'\n'

class Applicant(TestCase):
    def test_sender_uses_exact_archive_and_no_legacy_fallback(self):
        payload=fixture([('./'+MARKER,EXPECTED),('./'+MANIFEST,marker_line)])
        p=promotion(payload)
        policy=Policy('7','c'*40,'example','repo:Bonzar/craft:environment:'+BASE.prod.environment,'ABCD',frozenset({'8'}))
        frames=[]
        def exchange(cmd,frame,archive):
            frames.append(frame)
            self.assertEqual(cmd,'configured-prod-receive')
            if frame['op']=='terminal-challenge-v3':
                self.assertIsNone(archive);return dict(status='ok',nonce='7'*64)
            self.assertEqual(archive.read(),payload)
            self.assertTrue(frame['token'].startswith('urn:assertion-memory:configured:base:terminal:v3:'))
            return permit(p)
        with patch('pathlib.Path.open',return_value=io.BytesIO(payload)):
            self.assertEqual(operate_terminal(exchange,policy,p,Path('dummy'),get_token=lambda a:a,expected_mode='sidecar'),permit(p))
        self.assertEqual([f['op'] for f in frames],['terminal-challenge-v3','terminal-confirm-v3'])
        self.assertEqual(frames[0]['candidate'],frames[1]['candidate'])

    def test_bad_archive_seal_and_duplicate_denied(self):
        for items in ([(MANIFEST,marker_line)],
                      [(MARKER,EXPECTED),(MANIFEST,b'unknown\n')],
                      [(MARKER,EXPECTED),('./'+MARKER,EXPECTED),(MANIFEST,marker_line)]):
            payload=fixture(items)
            with self.assertRaises(ValueError):
                archive_contract(io.BytesIO(payload),promotion(payload))
        payload=fixture([(MANIFEST,b'legacy\n')])
        self.assertEqual(archive_contract(io.BytesIO(payload),promotion(payload)).mode,'legacy')
