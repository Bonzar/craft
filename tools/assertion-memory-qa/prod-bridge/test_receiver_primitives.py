"""Keep journal and payload primitives byte/AST equivalent to the pinned TEST baseline."""
import ast
from pathlib import Path
import unittest


class Primitives(unittest.TestCase):
    def test_functions_match_and_no_legacy_dispatch_is_shipped(self):
        root=Path(__file__).parent
        before=ast.parse((root.parent/'vm'/'receive.py').read_text())
        actual=(root/'receiver.py').read_text()
        self.assertNotIn('allow-activation',actual)
        a={n.name:ast.dump(n) for n in before.body if isinstance(n,ast.FunctionDef)}
        b={n.name:ast.dump(n) for n in ast.parse(actual).body if isinstance(n,ast.FunctionDef)}
        self.assertNotIn('main',b)
        self.assertNotIn('deploy',b)
        self.assertEqual(len(b),12)
        for name,body in b.items():
            self.assertEqual(body,a[name])
