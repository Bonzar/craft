"""Small real-SQLite contention fixtures: no external providers or personal data."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import threading
from unittest.mock import patch
import unittest
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.processing_pool import ProcessingPool

class LaneSQLiteBusyTest(unittest.TestCase):
    def test_short_writer_during_actual_maintenance_tick_does_not_stop_two_plus_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite3'
            with MemoryStore(str(path)) as store:
                service=MemoryService(store)
                for key in ('source-a','source-b','maintenance-c'):
                    service.queue.capture(CaptureInput('Synthetic conditional '+key,'fixture','fixture://'+key,'s',assertion_id=key,scope='s'))
                with store.transaction(write=True) as db:db.execute("UPDATE memory_capture_jobs SET phase='refresh' WHERE assertion_id='maintenance-c'")
            initialized=threading.Event();locked=threading.Event();release=threading.Event();mutex=threading.Lock();made=[0];lanes=[]
            def writer():
                initialized.wait(2)
                with sqlite3.connect(path,isolation_level=None) as db:
                    db.execute('BEGIN IMMEDIATE');locked.set();release.wait(.12);db.rollback()
            blocker=threading.Thread(target=writer);blocker.start()
            @contextmanager
            def factory(lane,observer):
                with MemoryStore(str(path)) as store:
                    engine=MemoryEngine(store)
                    worker=LinkWorker(MemoryService(store,engine).queue,engine,lane=lane,source_first=True,claim_observer=observer)
                    store._connection.execute('PRAGMA busy_timeout=5')
                    with mutex:
                        made[0]+=1;lanes.append(lane)
                        if made[0]==3:initialized.set()
                    class Adapter:
                        model=None
                        def run_once(self):
                            if not locked.wait(2):raise AssertionError('real SQLite writer did not acquire fixture lock')
                            return worker.run_once()
                        def seconds_until_work(self,*,with_model):return worker.seconds_until_work(with_model=with_model)
                    yield Adapter()
            pool=ProcessingPool(factory,source_workers=2,limit=3,idle_seconds=.02,poll_seconds=.01)
            try:result=pool.run()
            finally:release.set();blocker.join(2)
            self.assertEqual(result['reason'],'limit');self.assertEqual(result['completed'],3)
            self.assertCountEqual(lanes,['source','source','maintenance'])
            self.assertTrue(all(not thread.is_alive() for thread in pool.threads))
    def hold_writer(self,path,hold=.12):
        locked=threading.Event();release=threading.Event()
        def write():
            with sqlite3.connect(path,isolation_level=None) as db:
                db.execute('BEGIN IMMEDIATE');locked.set();release.wait(hold);db.rollback()
        thread=threading.Thread(target=write);thread.start();self.assertTrue(locked.wait(2))
        return release,thread
    def test_background_claim_and_failed_cleanup_wait_before_body_without_replaying_it(self):
        from prototypes.assertion_memory.storage import background_write_retry
        with tempfile.TemporaryDirectory() as directory,MemoryStore(directory+'/memory.sqlite3') as store:
            service=MemoryService(store);event=service.capture_message('s','m','fixture','user','Synthetic conditional source.',0,'fixture://one')['event']
            store._connection.execute('PRAGMA busy_timeout=5')
            release,writer=self.hold_writer(Path(store.path))
            try:
                with background_write_retry(max_wait_seconds=.5,poll_seconds=.005):
                    claim=service.queue._claim(with_model=True,source_first=True,lane='source')
            finally:release.set();writer.join(2)
            self.assertEqual(claim.job.id,event['id'])
            self.assertEqual(store._connection.execute('PRAGMA busy_timeout').fetchone()[0],5)
            release,writer=self.hold_writer(Path(store.path))
            try:
                with background_write_retry(max_wait_seconds=.5,poll_seconds=.005):
                    result=service.queue._fail(claim,RuntimeError('synthetic provider failure'))
            finally:release.set();writer.join(2)
            self.assertEqual(result.status,'pending')
            row=store._connection.execute('SELECT lease_token,attempts FROM memory_capture_jobs WHERE id=?',(event['id'],)).fetchone()
            self.assertIsNone(row['lease_token']);self.assertEqual(row['attempts'],1)
    def test_foreground_does_not_inherit_background_wait_policy(self):
        with tempfile.TemporaryDirectory() as directory,MemoryStore(directory+'/memory.sqlite3') as store:
            store._connection.execute('PRAGMA busy_timeout=5');release,writer=self.hold_writer(Path(store.path))
            entered=[]
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    with store.transaction(write=True):entered.append(True)
            finally:release.set();writer.join(2)
            self.assertEqual(entered,[]);self.assertEqual(store._connection.execute('PRAGMA busy_timeout').fetchone()[0],5)
    def test_cancel_and_exhaustion_are_bounded_with_real_writer_still_holding_lock(self):
        from prototypes.assertion_memory.storage import background_write_retry,BackgroundWriteCancelled
        with tempfile.TemporaryDirectory() as directory,MemoryStore(directory+'/memory.sqlite3') as store:
            store._connection.execute('PRAGMA busy_timeout=5')
            for cancelled in (lambda:True,lambda:False):
                release,writer=self.hold_writer(Path(store.path),hold=2);entered=[]
                try:
                    expected=BackgroundWriteCancelled if cancelled() else sqlite3.OperationalError
                    with self.assertRaises(expected) as error:
                        with background_write_retry(is_cancelled=cancelled,max_wait_seconds=.02,poll_seconds=.002):
                            with store.transaction(write=True):entered.append(True)
                    if not cancelled():self.assertEqual(error.exception.sqlite_errorcode & 0xff,sqlite3.SQLITE_BUSY)
                    self.assertFalse(release.is_set());self.assertEqual(entered,[])
                    self.assertEqual(store._connection.execute('PRAGMA busy_timeout').fetchone()[0],5)
                finally:release.set();writer.join(2)
    def test_cancel_interrupts_an_already_attempted_real_write_admission(self):
        from prototypes.assertion_memory.storage import background_write_retry,BackgroundWriteCancelled
        with tempfile.TemporaryDirectory() as directory,MemoryStore(directory+'/memory.sqlite3') as store:
            store._connection.execute('PRAGMA busy_timeout=5')
            release,writer=self.hold_writer(Path(store.path),hold=2)
            attempted=threading.Event();cancelled=threading.Event();body=[]
            store._connection.set_trace_callback(lambda sql:attempted.set() if sql=='BEGIN IMMEDIATE' else None)
            def cancel():
                if attempted.wait(2):cancelled.set()
            canceller=threading.Thread(target=cancel);canceller.start()
            try:
                with self.assertRaises(BackgroundWriteCancelled),background_write_retry(is_cancelled=cancelled.is_set,max_wait_seconds=.5,poll_seconds=.005):
                    with store.transaction(write=True):body.append(True)
                self.assertTrue(attempted.is_set());self.assertTrue(cancelled.is_set());self.assertEqual(body,[])
                self.assertFalse(release.is_set());self.assertEqual(store._connection.execute('PRAGMA busy_timeout').fetchone()[0],5)
            finally:
                store._connection.set_trace_callback(None);release.set();writer.join(2);canceller.join(2)
    def test_failed_cleanup_cannot_reset_an_exhausted_busy_episode(self):
        from prototypes.assertion_memory.storage import background_write_retry
        with tempfile.TemporaryDirectory() as directory,MemoryStore(directory+'/memory.sqlite3') as store:
            store._connection.execute('PRAGMA busy_timeout=5');release,writer=self.hold_writer(Path(store.path),hold=2)
            attempts=[];store._connection.set_trace_callback(lambda sql:attempts.append(sql) if sql=='BEGIN IMMEDIATE' else None)
            try:
                with background_write_retry(max_wait_seconds=.02,poll_seconds=.002):
                    with self.assertRaises(sqlite3.OperationalError):
                        with store.transaction(write=True):self.fail('locked write body reached')
                    before=len(attempts)
                    with self.assertRaises(sqlite3.OperationalError):
                        with store.transaction(write=True):self.fail('cleanup restarted lock budget')
                    self.assertEqual(len(attempts),before)
            finally:store._connection.set_trace_callback(None);release.set();writer.join(2)
    def test_constructor_busy_retries_and_closes_each_partial_store(self):
        initialized=threading.Event();locked=threading.Event();release=threading.Event();attempts=[];closed=[]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite3'
            with MemoryStore(str(path)):pass
            def writer():
                initialized.wait(2)
                with sqlite3.connect(path,isolation_level=None) as db:
                    db.execute('BEGIN IMMEDIATE');locked.set();release.wait(.12);db.rollback()
            blocker=threading.Thread(target=writer);blocker.start()
            class ObservedStore(MemoryStore):
                def close(self):closed.append(id(self));super().close()
            @contextmanager
            def factory(lane,observer):
                with ObservedStore(str(path)) as store:
                    attempts.append(id(store));store._connection.execute('PRAGMA busy_timeout=5')
                    initialized.set()
                    if not locked.wait(2):raise AssertionError('constructor fixture writer missing')
                    store._connection.execute('BEGIN IMMEDIATE');store._connection.rollback()
                    class Empty:
                        model=None
                        def run_once(self):return None
                        def seconds_until_work(self,*,with_model):return None
                    yield Empty()
            try:
                pool=ProcessingPool(factory,source_workers=2,idle_seconds=.02,poll_seconds=.002,write_retry_seconds=.5,write_retry_poll_seconds=.005)
                result=pool.run()
            finally:initialized.set();release.set();blocker.join(2)
            self.assertEqual(result['reason'],'idle');self.assertGreater(len(attempts),3)
            self.assertCountEqual(attempts,closed)
    def test_expired_claim_is_not_revived_after_waiting_for_writer(self):
        from prototypes.assertion_memory.storage import background_write_retry
        from prototypes.assertion_memory.queue import LeaseLost
        with tempfile.TemporaryDirectory() as directory,MemoryStore(directory+'/memory.sqlite3') as store:
            service=MemoryService(store);service.capture_message('s','m','fixture','user','Synthetic source.',0,'fixture://source')
            queue=CaptureQueue(store,lease_seconds=.02);claim=queue._claim(with_model=True,source_first=True,lane='source')
            store._connection.execute('PRAGMA busy_timeout=5');release,writer=self.hold_writer(Path(store.path),hold=.08)
            try:
                with self.assertRaises(LeaseLost),background_write_retry(max_wait_seconds=.5,poll_seconds=.005):
                    queue._fail(claim,RuntimeError('synthetic'))
            finally:release.set();writer.join(2)
            row=store._connection.execute('SELECT lease_token,status FROM memory_capture_jobs WHERE id=?',(claim.job.id,)).fetchone()
            self.assertEqual(row['lease_token'],claim.token);self.assertEqual(row['status'],'running')
    def test_nonbusy_sql_error_and_body_errors_are_never_retried(self):
        from prototypes.assertion_memory.storage import background_write_retry
        calls=[]
        with tempfile.TemporaryDirectory() as directory,MemoryStore(directory+'/memory.sqlite3') as store:
            with self.assertRaises(sqlite3.OperationalError) as error,background_write_retry(max_wait_seconds=.1,poll_seconds=.005):
                with store.transaction(write=True) as db:
                    calls.append(True);db.execute('SELECT * FROM deliberately_missing_table')
            self.assertEqual(len(calls),1);self.assertEqual(error.exception.sqlite_errorcode,sqlite3.SQLITE_ERROR)
            self.assertFalse(store._connection.in_transaction)
            self.assertEqual(store._connection.execute('PRAGMA busy_timeout').fetchone()[0],5000)
    def test_cancelled_background_context_cannot_reach_a_new_write_body(self):
        from prototypes.assertion_memory.storage import background_write_retry,BackgroundWriteCancelled
        with tempfile.TemporaryDirectory() as directory,MemoryStore(directory+'/memory.sqlite3') as store:
            reached=[]
            with self.assertRaises(BackgroundWriteCancelled),background_write_retry(is_cancelled=lambda:True):
                with store.transaction(write=True):reached.append(True)
            self.assertEqual(reached,[])
            # Leaving the context restores ordinary foreground access.
            with store.transaction(write=True) as db:db.execute("UPDATE memory_state SET version=version WHERE singleton=1")

if __name__=='__main__':unittest.main()
