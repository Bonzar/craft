import asyncio
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
from unittest import IsolatedAsyncioTestCase

from .query_jobs import QueryJobs
from .storage import MemoryStore


class InteractiveQueryJobs(IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / 'synthetic.sqlite3')
        with MemoryStore(self.db):
            pass

    async def test_disconnect_and_check_the_same_result_without_restarting(self):
        entered, finish = Event(), Event()
        self.addCleanup(finish.set)
        invocations = []

        def run(query):
            invocations.append(query)
            entered.set()
            if not finish.wait(5):
                raise RuntimeError('fixture was not released')
            return {'answer': 'Сеанс в 19:30.', 'answer_accepted': True}

        jobs = QueryJobs(self.db, run, lambda **_: None, wait_seconds=0.10)
        async with jobs.lifespan():
            origin = asyncio.create_task(jobs(query='Когда сеанс?', query_request_id='fixture-1'))
            self.assertTrue(await asyncio.to_thread(entered.wait,1))
            origin.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await origin
            same = await jobs(query='Когда сеанс?', query_request_id='fixture-1')
            self.assertEqual(same['query_state'],'running')
            self.assertFalse(same['answer_accepted'])
            key = same['query_job_id']
            with self.assertRaises(ValueError):
                await jobs(query='Другой вопрос?', query_request_id='fixture-1')
            self.assertEqual((await jobs(query_job_id=key))['query_state'],'running')
            finish.set()
            for _ in range(30):
                result = await jobs(query_job_id=key)
                if result['query_state']=='completed':
                    break
                await asyncio.sleep(0.03)
            self.assertEqual(result['answer'],'Сеанс в 19:30.')
            self.assertEqual(result['query_job_id'],key)
            self.assertEqual((await jobs(query='Когда сеанс?',query_request_id='fixture-1')),result)
            self.assertEqual(invocations,['Когда сеанс?'])

        # The completed result survives a new HTTP/worker instance.
        restarted = QueryJobs(self.db, lambda **_: self.fail('must not repeat the completed query'),
                              lambda **_: None)
        async with restarted.lifespan():
            self.assertEqual(await restarted(query_job_id=key),result)

    async def test_ready_answer_without_waiting_behind_generation(self):
        entered, finish = Event(), Event()
        self.addCleanup(finish.set)
        calls=[]
        def run(query):
            calls.append(query)
            entered.set()
            finish.wait(5)
            return {'answer_accepted': True}
        def ready(query):
            if query=='Уже отвечали?':
                return {'answer_accepted': True,'answer':'Да.'}
            return None
        jobs = QueryJobs(self.db,run,ready,wait_seconds=0.05)
        async with jobs.lifespan():
            pending = await jobs(query='Долгий вопрос?')
            self.assertIn(pending['query_state'],('queued','running'))
            self.assertTrue(await asyncio.to_thread(entered.wait,1))
            direct = await jobs(query='Уже отвечали?')
            self.assertEqual(direct,{'answer_accepted':True,'answer':'Да.','query_state':'completed'})
            self.assertEqual(calls,['Долгий вопрос?'])
            finish.set()

    async def test_worker_restart_reports_interrupted_without_silent_reexecution(self):
        jobs = QueryJobs(self.db,lambda **_: self.fail('must not repeat interrupted model work'),
                         lambda **_: None)
        jobs._init()
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO memory_interactive_queries VALUES('old',NULL,'{}','running',NULL,0)")
        async with jobs.lifespan():
            result = await jobs(query_job_id='old')
            self.assertEqual(result['query_state'],'failed')
            self.assertEqual(result['answer_error']['code'],'worker_interrupted')
