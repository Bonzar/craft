import ast
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch

PATH=Path(__file__).with_name('prod_service_hook_fenced_diag.py')

class Diagnostics(TestCase):
    def test_main_error_sends_fixed_local_stage_only(self):
        syslog=Mock()
        with patch.dict(sys.modules, {'syslog':SimpleNamespace(syslog=syslog,LOG_WARNING=4)}):
            with patch('sys.argv',['fixture','intentionally-unsupported','not-a-real-path']):
                with self.assertRaisesRegex(SystemExit,'^Scoped base production hook failed closed$'):
                    runpy.run_path(str(PATH),run_name='__main__')
        syslog.assert_called_once_with(4,'assertion-production-hook: entry')

    def test_all_phase_names_are_fixed_or_mcp_graph_role_only(self):
        tree=ast.parse(PATH.read_bytes())
        calls=[n.args[0] for n in ast.walk(tree)
               if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='phase']
        self.assertGreaterEqual(len(calls),20)
        for expr in calls:
            if isinstance(expr,ast.Constant):
                self.assertRegex(expr.value,r'^[a-z-]+$')
            else:
                self.assertEqual([n.id for n in ast.walk(expr) if isinstance(n,ast.Name)],['role'])
        self.assertIn("for i,role in enumerate(('mcp','graph')):",PATH.read_text())

if __name__=='__main__': main()
