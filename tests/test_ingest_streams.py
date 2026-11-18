"""Synthetic operational delivery frontiers; no graph, source prose or provider."""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from prototypes.assertion_memory import ingest_streams as api


class IngestStreamsTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE memory_raw_events(id TEXT PRIMARY KEY,session_id TEXT NOT NULL)')
        self.now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        self.stream = self.write(api.register, 'laptop-a', 'session')

    def write(self, fn, *args, **kwargs):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            value = fn(self.db, *args, now=self.now, **kwargs)
            self.db.commit()
            return value
        except BaseException:
            self.db.rollback()
            raise

    def report(self, seq=1, observed=0, state='current', gaps=0, lifecycle='active', **kw):
        return self.write(api.report, self.stream['stream_id'], self.stream['epoch'], seq,
                          observed, state, gaps, lifecycle=lifecycle, **kw)

    def deliver(self, seq, event=None, epoch=None):
        event = event or 'event-' + str(seq)
        self.db.execute('INSERT OR IGNORE INTO memory_raw_events VALUES (?,?)', (event, 'session'))
        return self.write(api.record_delivery, {'stream_id': self.stream['stream_id'],
            'epoch': epoch or self.stream['epoch'], 'seq': seq}, event, 'session')

    def status(self):
        return api.transport_status(self.db, now=self.now)

    def test_read_only_status_does_not_create_schema(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        self.assertEqual(api.transport_status(db, now=self.now)['coverage'], 'unobserved')
        self.assertEqual(db.execute('SELECT count(*) FROM sqlite_master').fetchone()[0], 0)

    def test_register_is_idempotent_and_unknown_is_bounded(self):
        self.assertEqual(self.write(api.register, 'laptop-a', 'session'), self.stream)
        self.assertTrue(self.status()['transport_limit'])
        self.now += timedelta(seconds=api.LEASE_SECONDS + 1)
        status = self.status()
        self.assertFalse(status['transport_limit'])
        self.assertEqual(status['coverage'], 'unobserved')
        self.assertEqual(status['disconnected_stream_count'], 1)

    def test_out_of_order_delivery_1_3_2_closes_only_exact_prefix(self):
        self.report(observed=3)
        self.assertEqual(self.deliver(1)['acked_through'], 1)
        self.assertEqual(self.deliver(3)['acked_through'], 1)
        self.assertEqual(self.status()['known_pending_count'], 1)
        self.assertEqual(self.deliver(2)['acked_through'], 3)
        self.assertFalse(self.status()['transport_limit'])

    def test_lost_ack_replays_identical_receipt_without_new_revision(self):
        first = self.deliver(1)
        self.assertEqual(self.deliver(1), first)
        with self.assertRaises(ValueError):
            self.deliver(1, event='different-event')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_ingest_receipts').fetchone()[0], 1)

    def test_same_event_cannot_fill_two_slots_in_one_stream_but_can_be_seen_by_another_device(self):
        self.deliver(1, event='shared-event')
        with self.assertRaises(ValueError): self.deliver(2, event='shared-event')
        other = self.write(api.register, 'laptop-b', 'session')
        receipt = self.write(api.record_delivery, {'stream_id':other['stream_id'],
            'epoch':other['epoch'],'seq':1}, 'shared-event', 'session')
        self.assertEqual(receipt['acked_through'], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_ingest_receipts').fetchone()[0], 2)

    def test_stale_explicit_catching_up_remains_known_incomplete(self):
        self.report(state='catching_up')
        self.now += timedelta(days=1)
        self.assertTrue(self.status()['transport_limit'])
        self.assertEqual(self.status()['known_pending_count'], 0)

    def test_old_report_and_old_epoch_cannot_clear_pending(self):
        self.report(seq=2, observed=2, state='gapped', gaps=1)
        with self.assertRaises(ValueError): self.report(seq=1)
        with self.assertRaises(ValueError): self.report(seq=2, observed=2)
        old = self.stream['epoch']
        self.stream = self.write(api.register, 'laptop-a', 'session', replace_epoch=old)
        with self.assertRaises(ValueError): self.deliver(1, epoch=old)
        self.assertEqual(self.status()['source_gap_count'], 1)
        self.assertEqual(self.status()['known_pending_count'], 2)
        self.assertNotEqual(self.stream['epoch'], old)

    def test_duplicate_heartbeat_does_not_extend_lease(self):
        first = self.report()
        self.now += timedelta(seconds=40)
        self.assertEqual(self.report(), first)
        self.now += timedelta(seconds=21)
        self.assertEqual(self.status()['coverage'], 'unobserved')

    def test_closed_clean_stream_does_not_become_stale_blocker(self):
        self.report(observed=1)
        with self.assertRaises(ValueError): self.report(seq=2, observed=1, lifecycle='closed')
        self.deliver(1)
        self.report(seq=2, observed=1, lifecycle='closed')
        self.now += timedelta(days=365)
        status = self.status()
        self.assertFalse(status['transport_limit'])
        self.assertEqual(status['disconnected_stream_count'], 0)
        self.assertEqual(status['closed_stream_count'], 1)
        with self.assertRaises(ValueError): self.deliver(2)
        self.report(seq=3, observed=2)
        self.assertEqual(self.deliver(2)['acked_through'], 2)

    def test_stale_pending_gaps_and_scanning_are_not_erased_by_ttl(self):
        self.report(observed=2, state='gapped', gaps=1)
        self.now += timedelta(days=365)
        status = self.status()
        self.assertTrue(status['transport_limit'])
        self.assertEqual(status['known_pending_count'], 2)
        self.assertEqual(status['source_gap_count'], 1)
        self.assertEqual(status['coverage'], 'incomplete')

    def test_capture_receipt_and_raw_insert_rollback_together(self):
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute("INSERT INTO memory_raw_events VALUES ('rolled-back','session')")
        api.record_delivery(self.db, {'stream_id':self.stream['stream_id'],
            'epoch':self.stream['epoch'],'seq':1}, 'rolled-back', 'session', now=self.now)
        self.db.rollback()
        self.assertIsNone(self.db.execute("SELECT 1 FROM memory_raw_events WHERE id='rolled-back'").fetchone())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_ingest_receipts').fetchone()[0], 0)
        self.assertEqual(self.status()['known_pending_count'], 0)

    def test_wrong_session_and_nontransactional_mutations_are_rejected(self):
        self.db.execute("INSERT INTO memory_raw_events VALUES ('foreign','other-session')")
        with self.assertRaises(ValueError):
            self.write(api.record_delivery, {'stream_id':self.stream['stream_id'],
                'epoch':self.stream['epoch'],'seq':1}, 'foreign', 'session')
        with self.assertRaises(ValueError): api.register(self.db,'laptop-b','session',now=self.now)

    def test_transport_health_is_global_and_other_device_cannot_close_this_stream(self):
        other = self.write(api.register,'laptop-b','session')
        self.write(api.report,other['stream_id'],other['epoch'],1,0,'current',0,lifecycle='closed')
        self.report(observed=1)
        self.assertTrue(self.status()['transport_limit'])
        self.assertEqual(self.status()['known_pending_count'],1)
        self.assertEqual(self.status()['stream_count'],2)


if __name__ == '__main__': unittest.main()
