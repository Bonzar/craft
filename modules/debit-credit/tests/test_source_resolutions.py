"""Подтверждение действует на точную операцию, не превращается в общее правило."""

import copy
import unittest

from adapters.source_resolutions import apply_resolutions
from test_matching import operation


def snapshot(*records):
    return {'schema_version': 1, 'adapter': 'test-source', 'input_count': len(records),
            'records': list(records), 'rejected': [], 'issues': [], 'coverage': 'unverified',
            'ready': False}


def resolution(record, **changes):
    return {'source': record['source'], 'id': record['source_id'],
            'expect': {key: record[key] for key in
                       ('timestamp', 'amount', 'currency', 'account', 'status', 'cleared')},
            'set': {}, 'reason': 'Подтверждено человеком для этого платежа', **changes}


class SourceResolutionsTest(unittest.TestCase):
    def test_account_resolution_only_changes_the_named_operation(self):
        one = operation('one', account=None, issues=['unknown_account', 'other_issue'])
        two = operation('two', account=None, issues=['unknown_account'])
        original = snapshot(one, two)
        saved = copy.deepcopy(original)
        result = apply_resolutions(original, [resolution(one, set={'account': 'Карта'})])
        self.assertEqual(result['records'][0]['account'], 'Карта')
        self.assertEqual(result['records'][0]['issues'], ['other_issue'])
        self.assertIsNone(result['records'][1]['account'])
        self.assertEqual(result['records'][0]['resolution']['before']['account'], None)
        self.assertEqual(original, saved)
        self.assertEqual(len(result['records']), 2)

    def test_alias_keeps_both_original_amounts_currencies_and_ids(self):
        rub = operation('rub', amount='-1696.14')
        byn = operation('byn', amount='-64.2', currency='BYN', account='Other')
        result = apply_resolutions(snapshot(rub, byn), [resolution(byn,
            same_operation_as={'source': 'yabank', 'id': 'rub'})])
        self.assertEqual([r['amount'] for r in result['records']], ['-1696.14', '-64.2'])
        self.assertEqual(result['records'][1]['resolution']['same_operation_as']['id'], 'rub')

    def test_changed_source_values_cannot_reuse_an_old_confirmation(self):
        row = operation()
        for field, value in [('amount', '-99'), ('timestamp', '2026-07-05T10:00:00+03:00')]:
            changed = {**row, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                apply_resolutions(snapshot(changed), [resolution(row, set={'account': 'Card'})])

    def test_missing_repeated_and_duplicate_confirmation_targets_are_rejected(self):
        row = operation()
        spec = resolution(row, set={'account': 'Card'})
        for source, specs in [(snapshot(), [spec]), (snapshot(row, row), [spec]),
                              (snapshot(row), [spec, spec])]:
            with self.subTest(count=source['input_count']), self.assertRaises(ValueError):
                apply_resolutions(source, specs)

    def test_missing_alias_target_and_alias_chain_are_rejected(self):
        one, two = operation('one'), operation('two')
        first = resolution(one, same_operation_as={'source': 'yabank', 'id': 'two'})
        second = resolution(two, same_operation_as={'source': 'yabank', 'id': 'one'})
        for source, specs in [(snapshot(one), [first]), (snapshot(one, two), [first, second])]:
            with self.assertRaises(ValueError):
                apply_resolutions(source, specs)

    def test_unsupported_mutations_and_unaccounted_input_are_rejected(self):
        row = operation()
        for change in ({'amount': '-99'}, {'account': ''}, {'kind': 'guess'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                apply_resolutions(snapshot(row), [resolution(row, set=change)])
        broken = snapshot(row)
        broken['input_count'] = 2
        with self.assertRaises(ValueError):
            apply_resolutions(broken, [])

    def test_source_level_failures_survive_record_confirmation(self):
        row = operation(account=None, issues=['unknown_account'])
        source = snapshot(row)
        source['issues'] = [{'code': 'incomplete_page'}, {'locator': 1, 'code': 'unknown_account'}]
        result = apply_resolutions(source, [resolution(row, set={'account': 'Карта'})])
        self.assertEqual(result['issues'], [{'code': 'incomplete_page'}])

    def test_confirmation_does_not_survive_a_changed_completion_status(self):
        row = operation()
        changed = {**row, 'status': 'canceled', 'cleared': False}
        with self.assertRaises(ValueError):
            apply_resolutions(snapshot(changed), [resolution(row, set={'account': 'Карта'})])
