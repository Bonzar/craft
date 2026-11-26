from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from . import browser_prod_binding as binding

class ProdBrowserBindingTest(TestCase):
    def test_canonical_lane_socket_and_writer_required_before_bind(self):
        cfg=SimpleNamespace(db=binding.PROD.db)
        with patch.object(binding.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=986)):
            with (patch.object(binding.os,'getuid',return_value=986),
                  patch.object(binding.os,'geteuid',return_value=986),
                  patch.object(binding,'exact_database') as database,
                  patch.object(binding,'_identity') as identity):
                with self.assertRaises(ValueError):
                    binding.configured_prod_browser_lifespan(cfg,private_port=8893,interactive_enabled=False)
                with self.assertRaises(ValueError):
                    binding.configured_prod_browser_lifespan(SimpleNamespace(db=Path('/tmp/fake.sqlite3')),
                        private_port=8903,interactive_enabled=False)
                database.assert_not_called()
                identity.assert_not_called()
                self.assertTrue(callable(binding.configured_prod_browser_lifespan(
                    cfg,private_port=8903,interactive_enabled=False)))
                identity.assert_called_once_with(binding.PROD.root,'prod')

            with patch.object(binding.os,'geteuid',return_value=0):
                with self.assertRaises(ValueError):
                    binding.configured_prod_browser_lifespan(cfg,private_port=8903,interactive_enabled=False)

    def test_switch_dark_leaves_test_and_prod_without_marker_unchanged(self):
        with (patch.object(binding,'_read_switch',return_value=False) as gate,
              patch.object(binding,'configured_prod_browser_lifespan') as enable):
            self.assertIsNone(binding.installed_prod_browser_lifespan('/unscoped/test/http.json',None,
                private_port=8893,on_failure=lambda:None))
            gate.assert_not_called()
            self.assertIsNone(binding.installed_prod_browser_lifespan(binding.PROD_HTTP_CONFIG,None,
                private_port=8903,on_failure=lambda:None))
            gate.assert_called_once_with(binding.PROD_SWITCH)
            enable.assert_not_called()

    def test_legacy_still_running_denies_entry_before_graph_state_opens(self):
        cfg=SimpleNamespace(db=binding.PROD.db)
        with (patch.object(binding.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=986)),
              patch.object(binding.os,'getuid',return_value=986),
              patch.object(binding.os,'geteuid',return_value=986),
              patch.object(binding,'exact_database'),
              patch.object(binding,'_identity'),
              patch.object(binding,'_require_legacy_graph_stopped',side_effect=ValueError('still running')),
              patch('apps.graph_viewer.server.app.create_app') as graph):
            open_browser=binding.configured_prod_browser_lifespan(cfg,private_port=8903,
                                                                  interactive_enabled=False)
            with self.assertRaises(ValueError):
                open_browser()
            graph.assert_not_called()

    def test_exact_sealed_capability_required_and_legacy_absence_stays_dark(self):
        from hashlib import sha256
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            root=Path(folder)
            candidate=root/'sealed-release'
            candidate.mkdir()
            (root/'current').symlink_to(candidate)
            fixture={binding._MAIN:b'main',binding._BINDING:b'binding'}
            for name,raw in fixture.items():
                dest=candidate/name
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(raw)
            def manifest():
                (candidate/'SHA256SUMS').write_text(''.join(
                    f'{sha256(raw).hexdigest()}  ./{name}\n' for name,raw in fixture.items()))
            with (patch.object(binding,'__file__',str(candidate/binding._BINDING)),
                  patch.object(binding,'_identity')):
                manifest()
                self.assertFalse(binding._capable_release(root))
                (candidate/binding._CAPABILITY).write_bytes(binding._CAPABILITY_V1)
                with self.assertRaises(ValueError):
                    binding._capable_release(root) # existing unsealed marker
                fixture[binding._CAPABILITY]=binding._CAPABILITY_V1
                manifest()
                self.assertTrue(binding._capable_release(root))
                checksums=candidate/'SHA256SUMS'
                checksums.write_text(checksums.read_text()+
                    f'{sha256(fixture[binding._CAPABILITY]).hexdigest()}  {binding._CAPABILITY}\n')
                with self.assertRaises(ValueError):
                    binding._capable_release(root)
                manifest()
                (candidate/binding._MAIN).write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    binding._capable_release(root)
