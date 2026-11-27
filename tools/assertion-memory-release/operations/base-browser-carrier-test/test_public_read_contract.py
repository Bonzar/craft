import json
import unittest

import public_read_contract as c


OLD = ('assertion-memory-v0.1.37', 'a'*40, 'b'*64)
NEW = ('assertion-memory-v0.1.38', 'c'*40, 'd'*64)
NEW_RECEIPT = {'status':'ok', 'environment':'test', 'revision':NEW[1], 'sha256':NEW[2]}


class CarrierRead(unittest.TestCase):
    def read(self, actual, **other):
        def call(url):
            self.assertEqual(url, c.URL)
            value = dict(stage='test', release_version=actual[0], release_generation=c.identity(*actual))
            value.update(other)
            return url, 200, 'application/json', json.dumps(value).encode()
        return call

    def test_exact_authenticated_activation_pair(self):
        self.assertEqual(c.activation(NEW_RECEIPT, NEW, self.read(NEW)), c.identity(*NEW))

    def test_receiver_success_does_not_accept_gateway_serving_previous(self):
        with self.assertRaisesRegex(ValueError, 'stale_or_foreign'):
            c.activation(NEW_RECEIPT, NEW, self.read(OLD))

    def test_same_tag_different_archive_rejected(self):
        with self.assertRaisesRegex(ValueError, 'stale_or_foreign'):
            c.activation(NEW_RECEIPT, NEW, self.read((NEW[0], NEW[1], OLD[2])))

    def test_foreign_lane_or_wrong_receipt_rejected(self):
        with self.assertRaisesRegex(ValueError, 'stale_or_foreign'):
            c.activation(NEW_RECEIPT, NEW, self.read(NEW, stage='production'))
        with self.assertRaisesRegex(ValueError, 'unbound'):
            c.activation(dict(NEW_RECEIPT, sha256=OLD[2]), NEW, lambda _: self.fail('unexpected call'))

    def test_inverse_checks_old_binding_and_rejects_current_candidate(self):
        self.assertEqual(c.restored_view(NEW, OLD, self.read(OLD)), c.identity(*OLD))
        with self.assertRaisesRegex(ValueError, 'stale_or_foreign'):
            c.restored_view(NEW, OLD, self.read(NEW))
        with self.assertRaisesRegex(ValueError, 'unchanged'):
            c.restored_view(OLD, OLD, lambda _: self.fail('unexpected call'))

    def test_unauthorized_redirect_overflow_duplicate_and_wrong_type(self):
        for url, status, media, body in (
            (c.URL, 401, 'application/json', b'{}'), (c.URL, 200, 'text/html', b'{}'),
            (c.URL+'/else', 200, 'application/json', b'{}'),
            (c.URL, 200, 'application/json', b' '*(c.LIMIT+1)),
            (c.URL, 200, 'application/json', b'{"stage":"production","stage":"test"}')):
            with self.subTest(status=status, media=media, length=len(body)), self.assertRaises(ValueError):
                c.activation(NEW_RECEIPT, NEW, lambda _: (url,status,media,body))
