import ast
from unittest import TestCase
from unittest.mock import Mock,patch
import compile_stock_host_adapters as c

class AdapterContract(TestCase):
    def test_guard_and_hook_export_original_fixed_signatures(self):
        g=ast.parse(c.GUARD_ADAPTER); h=ast.parse(c.HOOK_ADAPTER)
        f=g.body[-1]
        self.assertEqual(f.name,'_base_browser_restart_selected')
        self.assertEqual(len(f.args.args)+len(f.args.kwonlyargs),0)
        self.assertEqual([type(n).__name__ for n in g.body+h.body],['FunctionDef']*5)
        self.assertEqual(h.body[-1].name,'_base_browser_sidecar_check_live')
        for source in (c.GUARD_ADAPTER,c.HOOK_ADAPTER):
            compile(source,'adapter','exec')
            self.assertIn(b'/etc/assertion-release/configured-base-prod/base-terminal-binding',source)

    def test_dark_path_preserves_inherited_restart_without_adapter_directory(self):
        native=Mock(return_value='native')
        scope={'NEW':('base.service','graph.service'),'run':native,'userctl':lambda _:['systemctl','--user']}
        exec(compile(c.GUARD_ADAPTER,'test','exec'),scope)
        owner={}
        exec(compile(c.HOOK_ADAPTER,'test','exec'),owner)
        with patch('pathlib.Path.lstat',side_effect=FileNotFoundError):
            self.assertEqual(scope['_base_browser_restart_selected'](),'native')
            self.assertIs(owner['_base_browser_sidecar_enabled'](),False)
        native.assert_called_once_with(['systemctl','--user','restart','base.service','graph.service'],timeout=18)
