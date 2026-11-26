import asyncio
from contextlib import contextmanager
import unittest

from .browser_listener import browser_listener

class FakeListener:
    def __init__(self):
        self.closed = False
    @contextmanager
    def bind(self, port):
        assert port == 18908
        try:
            yield self
        finally:
            self.closed = True

class FakeServer:
    should_exit = False
    started = False
    def __init__(self, fail=False):
        self.fail = fail
    async def serve(self, *, sockets):
        assert not sockets[0].closed
        if self.fail:
            return
        self.started = True
        while not self.should_exit:
            await asyncio.sleep(0)

class ListenerTest(unittest.IsolatedAsyncioTestCase):
    async def test_ready_before_yield_and_closed_on_stop(self):
        f = FakeListener(); server = FakeServer()
        async with browser_listener(lambda: None, 18908, _bind=f.bind, _server=lambda _: server):
            self.assertTrue(server.started)
            self.assertFalse(f.closed)
        self.assertTrue(f.closed)
        self.assertTrue(server.should_exit)

    async def test_failed_child_cannot_claim_base_ready(self):
        f = FakeListener(); server = FakeServer(fail=True)
        with self.assertRaises(ExceptionGroup):
            async with browser_listener(lambda: None, 18908, _bind=f.bind, _server=lambda _: server):
                self.fail('Failed side listener allowed Base readiness')
        self.assertTrue(f.closed)

    async def test_bind_conflict_aborts_before_starting_browser(self):
        def bind(_port):
            raise OSError('existing socket')
        with self.assertRaises(OSError):
            async with browser_listener(lambda: None, 18908, _bind=bind, _server=lambda _: self.fail()):
                self.fail()

    async def test_parent_cancellation_closes_child_socket(self):
        f = FakeListener(); server = FakeServer(); ready = asyncio.Event()
        async def run():
            async with browser_listener(lambda: None, 18908, _bind=f.bind, _server=lambda _: server):
                ready.set()
                await asyncio.Future()
        task = asyncio.create_task(run())
        await ready.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(f.closed)
        self.assertTrue(server.should_exit)
