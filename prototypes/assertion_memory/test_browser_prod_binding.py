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
                  patch.object(binding,'_identity') as identity,
                  patch.object(binding,'browser_listener') as listener):
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
                listener.assert_not_called()
            with patch.object(binding.os,'geteuid',return_value=0):
                with self.assertRaises(ValueError):
                    binding.configured_prod_browser_lifespan(cfg,private_port=8903,interactive_enabled=False)
