"""Сторона перевода по СБП: кому ушли деньги и в какой банк, по карточке каждого банка."""

import unittest

from adapters.cross_bank import party, same_person


class PartyTest(unittest.TestCase):
    def test_yandex_side_names_the_person_and_the_bank(self):
        row = {'source': 'yabank', 'bank_fields': [['Банк получателя', 'Сбербанк']],
               'raw': {'feed': {'title': {'plain': 'Иван П.'}}}}
        self.assertEqual(party(row), ('Иван П.', 'Сбербанк'))

    def test_yandex_operation_without_a_bank_is_not_a_person_transfer(self):
        row = {'source': 'yabank', 'bank_fields': [], 'raw': {'feed': {'title': {'plain': 'METRO'}}}}
        self.assertIsNone(party(row))

    def test_tbank_incoming_sbp_names_the_sender_bank(self):
        row = {'source': 'tbank', 'raw': {'senderDetails': 'Иван П.', 'brand': {'name': 'Яндекс'},
                                           'additionalInfo': [{'fieldName': 'Тип перевода',
                                                               'fieldValue': 'Система быстрых платежей'}]}}
        self.assertEqual(party(row), ('Иван П.', 'Яндекс'))

    def test_sber_sbp_names_only_the_person(self):
        row = {'source': 'sber', 'raw': {'form': 'P2PSBPInTransfer', 'correspondent': 'Иван Петрович П.'}}
        self.assertEqual(party(row), ('Иван Петрович П.', None))

    def test_sber_payment_is_not_a_person_transfer(self):
        self.assertIsNone(party({'source': 'sber', 'raw': {'form': 'ExtCardPayment', 'correspondent': 'METRO'}}))


class SamePersonTest(unittest.TestCase):
    def test_full_and_short_name_of_one_person_agree(self):
        self.assertTrue(same_person('Иван Петрович П', 'Иван П.'))

    def test_different_first_name_or_surname_initial_disagree(self):
        self.assertFalse(same_person('Иван П.', 'Пётр П.'))
        self.assertFalse(same_person('Иван П.', 'Иван С.'))

    def test_single_word_is_not_enough(self):
        self.assertFalse(same_person('Иван', 'Иван П.'))
