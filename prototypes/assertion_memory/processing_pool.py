"""One process, isolated SQLite/provider lanes, joined before supervisor unlock.

The caller owns the database flock and its heartbeat connection. Factories enter
inside each writing thread; neither engines, connections nor providers are shared.
A turn reserves the global attempt budget before it can claim durable work.
"""
from __future__ import annotations
from contextlib import ExitStack
from datetime import datetime,timedelta,timezone
import threading
import time
import logging
import sqlite3

from .storage import background_write_retry,BackgroundWriteCancelled,is_sqlite_busy

_LOG=logging.getLogger(__name__)

_TABLES={'source':('memory_capture_jobs','id'),'derived':('memory_derived_work','scope'),
         'name':('memory_view_name_work','view_id'),'thought':('memory_thought_work','thought_id')}


class ActiveClaims:
    def __init__(self):
        self._lock=threading.Lock();self._claims=set()

    def observe(self,event,kind,identity,token):
        if kind not in _TABLES or event not in ('claimed','released'):
            raise ValueError('unknown processing claim observer event')
        if not isinstance(identity,str) or not isinstance(token,str) or not identity or not token:
            raise ValueError('typed claim identity/token required')
        with self._lock:
            key=(kind,identity,token)
            if event=='claimed':self._claims.add(key)
            else:self._claims.discard(key)

    def snapshot(self):
        with self._lock:return tuple(self._claims)

    def renew(self,db,*,lease_seconds=300,now=None):
        """Call only on the supervisor's connection/transaction. Never revive revoked/expired work."""
        instant=now or datetime.now(timezone.utc)
        current=instant.isoformat(timespec='microseconds')
        until=(instant+timedelta(seconds=lease_seconds)).isoformat(timespec='microseconds')
        count=0
        for kind,identity,token in self.snapshot():
            table,column=_TABLES[kind]
            count+=db.execute(f'UPDATE {table} SET lease_until=? WHERE {column}=? '
                "AND status='running' AND lease_token=? AND lease_until>?",(until,identity,token,current)).rowcount
        return count


class ProcessingPool:
    """Run N source lanes plus one maintenance lane until idle or a shared limit.

    worker_factory(lane, observer) is a context manager entered inside a lane.
    Workers implement run_once() and seconds_until_work(with_model=...). Each
    run_once claims at most one job and reports claimed/released token ownership.
    heartbeat runs only in the calling supervisor thread, also while draining.
    """
    def __init__(self,worker_factory,*,source_workers=2,limit=0,idle_seconds=30,
                 poll_seconds=.1,heartbeat_seconds=30,heartbeat=None,claims=None,
                 write_retry_seconds=30,write_retry_poll_seconds=.05):
        if type(source_workers) is not int or not 1<=source_workers<=4:
            raise ValueError('source_workers must be between 1 and 4')
        if type(limit) is not int or limit<0:raise ValueError('limit must be nonnegative')
        if min(idle_seconds,poll_seconds,heartbeat_seconds,write_retry_seconds,write_retry_poll_seconds)<=0:raise ValueError('positive pool intervals required')
        self.factory=worker_factory;self.source_workers=source_workers;self.limit=limit
        self.idle_seconds=idle_seconds;self.poll_seconds=poll_seconds;self.heartbeat_seconds=heartbeat_seconds
        self.heartbeat=heartbeat;self.claims=claims or ActiveClaims()
        self.write_retry_seconds=write_retry_seconds;self.write_retry_poll_seconds=write_retry_poll_seconds
        self._condition=threading.Condition();self._construction=threading.Lock();self._stop=False
        self._completed=0;self._reserved=0;self._peak=0;self._errors=[]
        self._idle={index:False for index in range(source_workers+1)}
        self._last_work=time.monotonic();self._last_beat=0.0;self.threads=[]
        self._heartbeat_busy=False

    def _reserve(self,index):
        with self._condition:
            while not self._stop:
                if not self.limit or self._completed+self._reserved<self.limit:
                    self._reserved+=1;self._peak=max(self._peak,self._reserved)
                    self._idle[index]=False
                    return True
                if self._completed>=self.limit:return False
                self._condition.wait(self.poll_seconds)
            return False

    def _finish(self,index,worked,deadline=None):
        with self._condition:
            self._reserved-=1
            self._idle[index]=not worked and deadline is None
            if worked:self._completed+=1;self._last_work=time.monotonic()
            if self.limit and self._completed>=self.limit:self._stop=True
            self._condition.notify_all()

    def _open_worker(self,resources,index,lane):
        deadline=time.monotonic()+self.write_retry_seconds;has_warned=False
        while not self._stop:
            try:
                # A failed context entry unwinds the factory's partially created
                # resources. Only pre-yield construction is safe to restart.
                with self._construction:
                    if self._stop:return None
                    return resources.enter_context(self.factory(lane,self.claims.observe))
            except sqlite3.OperationalError as error:
                if not is_sqlite_busy(error):raise
                if self._stop:return None
                if time.monotonic()>=deadline:
                    _LOG.error('Processing constructor retry exhausted: lane=%s index=%s',lane,index)
                    raise
                if not has_warned:
                    _LOG.warning('Processing constructor deferred: lane=%s index=%s writer_busy',lane,index)
                    has_warned=True
                with self._condition:
                    if not self._stop:self._condition.wait(min(self.write_retry_poll_seconds,max(0,deadline-time.monotonic())))
        return None

    def _run_turn(self,index,worker):
        worked=False;deadline=0
        try:
            result=worker.run_once();worked=result is not None
            if not worked:deadline=worker.seconds_until_work(with_model=worker.model is not None)
        finally:self._finish(index,worked,deadline)
        if not worked:
            with self._condition:
                if not self._stop:self._condition.wait(self.poll_seconds)

    def _lane(self,index,lane):
        try:
            with background_write_retry(is_cancelled=lambda:self._stop,max_wait_seconds=self.write_retry_seconds,
                    poll_seconds=self.write_retry_poll_seconds),ExitStack() as resources:
                worker=self._open_worker(resources,index,lane)
                if worker is None:return
                while self._reserve(index):self._run_turn(index,worker)
        except BackgroundWriteCancelled as error:
            if not self._stop:self._fail_lane(index,lane,error)
        except BaseException as error:self._fail_lane(index,lane,error)

    def _fail_lane(self,index,lane,error):
        _LOG.error('Processing lane failed: lane=%s index=%s error_type=%s',lane,index,type(error).__name__)
        with self._condition:
            self._errors.append(error);self._stop=True;self._condition.notify_all()

    def _beat(self):
        now=time.monotonic()
        if self.heartbeat is not None and now-self._last_beat>=self.heartbeat_seconds:
            try:self.heartbeat()
            except sqlite3.OperationalError as error:
                # A competing writer can outlast SQLite's busy timeout. Retry on
                # the next poll; renewal still cannot revive an expired lease.
                code=getattr(error,'sqlite_errorcode',None)
                if code is None or code & 0xff != sqlite3.SQLITE_BUSY:raise
                if not self._heartbeat_busy:
                    _LOG.warning('Processing heartbeat deferred: SQLite writer busy')
                self._heartbeat_busy=True
                return
            self._last_beat=now;self._heartbeat_busy=False

    def _stop_and_join(self):
        with self._condition:self._stop=True;self._condition.notify_all()
        failure=None
        while any(thread.is_alive() for thread in self.threads):
            try:
                self._beat()
                for thread in self.threads:thread.join(timeout=self.poll_seconds)
            except BaseException as error:
                # Even cancellation/heartbeat failure must not release flock while
                # a writing thread survives. Expired tokens are publication-fenced.
                if failure is None:failure=error
        return failure

    def run(self):
        if self.threads:raise RuntimeError('a processing pool epoch cannot be restarted')
        reason='idle';failure=None
        try:
            for index in range(self.source_workers+1):
                lane='source' if index<self.source_workers else 'maintenance'
                thread=threading.Thread(target=self._lane,args=(index,lane),name=f'memory-{lane}-{index}',daemon=False)
                self.threads.append(thread);thread.start()
            while True:
                self._beat()
                with self._condition:
                    if self._errors:break
                    if self.limit and self._completed>=self.limit:reason='limit';break
                    idle=(self._reserved==0 and all(self._idle.values()) and not self.claims.snapshot())
                    if idle and time.monotonic()-self._last_work>=self.idle_seconds:
                        self._stop=True;self._condition.notify_all();break
                    self._condition.wait(self.poll_seconds)
        except BaseException as error:
            _LOG.error('Processing supervisor failed: error_type=%s',type(error).__name__)
            failure=error
        finally:
            joined_failure=self._stop_and_join()
            failure=failure or joined_failure
        if failure is not None:raise failure
        if self._errors:raise self._errors[0]
        return {'reason':reason,'completed':self._completed,'source_workers':self.source_workers,
                'maintenance_workers':1,'peak_reserved':self._peak}
