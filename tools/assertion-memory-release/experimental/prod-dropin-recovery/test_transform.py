import hashlib, os, pathlib, re, stat, types, unittest
from unittest.mock import patch

class UntrustedUnitDropIn(ValueError): pass
UNITS=('mcp.service','graph.service')
MCP_APPROVED_DROPIN=pathlib.Path('/etc/systemd/user/mcp.service.d/73-assertion-codex-route.conf')
MCP_DROPIN_PIN=pathlib.Path('/etc/assertion-release/production/mcp-codex-route.sha256')

def digest(data):
    return hashlib.sha256(data).hexdigest().encode()+b'\n'


class Transform(unittest.TestCase):
    def setUp(self):
        self.file={}
        def checked_file(path, **kwargs):
            if path not in self.file: raise FileNotFoundError
            return self.file[path]
        from render_hook import render
        original=b"""def trusted_unit_dropins(unit, raw):
    return checked_file(MCP_DROPIN_PIN) and MCP_APPROVED_DROPIN and UNITS and UntrustedUnitDropIn
"""
        replacement=pathlib.Path(__file__).with_name('transform_policy.txt').read_text()
        candidate=render(original,hashlib.sha256(original).hexdigest(),replacement)
        self.scope=globals()|{'checked_file':checked_file}
        self.assertNotIn("hmac",self.scope)
        exec(candidate,self.scope)
        self.mon=self.scope['MCP_MONIUM_DROPIN']
        self.monpin=self.scope['MCP_MONIUM_PIN']
        self.f=self.scope['trusted_unit_dropins']

    def test_none_old_two_without_weakening_graph(self):
        with patch('os.path.lexists',return_value=False),patch.object(pathlib.Path,'resolve',lambda p:p),patch.object(pathlib.Path,'lstat',return_value=types.SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0)):
            self.f(UNITS[0],None)
            self.f(UNITS[1],'')
            self.file[MCP_APPROVED_DROPIN]=b'old'
            self.file[MCP_DROPIN_PIN]=digest(b'old')
            self.f(UNITS[0],str(MCP_APPROVED_DROPIN))
            self.file[self.mon]=b'new'
            self.file[self.monpin]=digest(b'new')
            raw=f'{self.mon} {MCP_APPROVED_DROPIN}'
            self.f(UNITS[0],raw)
            for unit, other in [(UNITS[0],str(MCP_APPROVED_DROPIN)),(UNITS[0],raw+' /extra'),(UNITS[1],raw),(UNITS[0],f'{MCP_APPROVED_DROPIN} {self.mon}')]:
                with self.assertRaises(UntrustedUnitDropIn):self.f(unit,other)
            self.file[self.mon]=b'replaced'
            with self.assertRaises(UntrustedUnitDropIn):self.f(UNITS[0],raw)

    def test_dangling_pin_does_not_become_absent(self):
        with patch('os.path.lexists',return_value=True):
            with self.assertRaises(UntrustedUnitDropIn):self.f(UNITS[0],None)

    def test_missing_primary_pin_never_allows_monium_by_itself(self):
        self.file[self.monpin]=digest(b'new')
        with patch('os.path.lexists',return_value=False):
            with self.assertRaises(UntrustedUnitDropIn):self.f(UNITS[0],str(self.mon))

    def test_writable_parent_is_rejected(self):
        self.file[MCP_DROPIN_PIN]=digest(b'old')
        with patch('os.path.lexists',return_value=False),patch.object(pathlib.Path,'resolve',lambda p:p),patch.object(pathlib.Path,'lstat',return_value=types.SimpleNamespace(st_mode=stat.S_IFDIR|0o775,st_uid=0)):
            with self.assertRaises(UntrustedUnitDropIn):self.f(UNITS[0],str(MCP_APPROVED_DROPIN))

class Renderer(unittest.TestCase):
    def test_exact_source_and_preserve_other_code(self):
        from render_hook import render
        original = b'''import sys

def trusted_unit_dropins(unit, raw):
    return checked_file(MCP_DROPIN_PIN) and MCP_APPROVED_DROPIN and UNITS and UntrustedUnitDropIn

def marker():
    return 12345
'''
        expected = hashlib.sha256(original).hexdigest()
        replacement = pathlib.Path(__file__).with_name('transform_policy.txt').read_text()
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            render(original+b'\n', expected, replacement)
        built = render(original, expected, replacement)
        self.assertTrue(built.startswith(b'import sys\n\nMCP_MONIUM_DROPIN'))
        self.assertTrue(built.endswith(b'\ndef marker():\n    return 12345\n'))
