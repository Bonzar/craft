"""One installed activation source and no legacy production receiver."""
from pathlib import Path
import unittest
import base_payload
import receive_authorized


class SharedSource(unittest.TestCase):
    def test_no_receiver_copy_or_old_static_gate(self):
        source=Path(receive_authorized.__file__).parent
        self.assertFalse((source/'receiver.py').exists())
        self.assertIs(receive_authorized.base_payload,base_payload)
        self.assertIsNotNone(base_payload.common_deploy)
        self.assertNotIn('allow-activation',Path(receive_authorized.__file__).read_text())
