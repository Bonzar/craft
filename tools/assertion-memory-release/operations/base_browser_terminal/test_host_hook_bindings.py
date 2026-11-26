from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from host_hook_bindings import sidecar_check_live,BASE,GRAPH
from terminal_verifier import Process
from transition_witness import Release

def sidecar_record(): return Release('1'*40,'2'*64,'3'*64,'sidecar')

class NotListening(RuntimeError): pass

class Hook(TestCase):
    def test_sidecar_only_single_native_verdict(self):
        identity=sidecar_record()
        path=Path('/srv/assertion-release/production/releases')/(identity.revision+'-'+identity.archive)
        policy=SimpleNamespace(mcp_port=8903,graph_port=8901)
        pid=lambda unit:345 if unit==BASE else 0
        proc=Process(BASE,345,30,986,identity)
        with (patch('host_hook_bindings.verified_settled_identity',return_value=identity),
              patch('host_hook_bindings._process',return_value=(proc,{70,71})),
              patch('host_hook_bindings._read',return_value='bounded'),
              patch('host_hook_bindings.start_ticks',return_value=30),
              patch('host_hook_bindings._tcp',side_effect=[{8902:70,8903:71},{}])):
            self.assertEqual(sidecar_check_live(policy,path,986,trusted_pid=pid,not_listening=NotListening),(345,))
        for mapping, expected in (({8903:71},NotListening),({8901:69,8903:71},ValueError),({8902:99,8903:71},ValueError)):
            with (patch('host_hook_bindings.verified_settled_identity',return_value=identity),
                  patch('host_hook_bindings._process',return_value=(proc,{70,71})),
                  patch('host_hook_bindings._read',return_value='bounded'),
                  patch('host_hook_bindings.start_ticks',return_value=30),
                  patch('host_hook_bindings._tcp',side_effect=[mapping,{}])):
                with self.assertRaises(expected):
                    sidecar_check_live(policy,path,986,trusted_pid=pid,not_listening=NotListening)
