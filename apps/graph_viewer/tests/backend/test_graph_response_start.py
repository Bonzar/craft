"""Check the app header boundary while a separate relay holds the response."""
import asyncio
import unittest
from unittest.mock import patch

from apps.graph_viewer.server import graph_response_start as timing


class GraphResponseStartTest(unittest.IsolatedAsyncioTestCase):
    async def test_held_public_relay_is_not_counted_as_late_application(self):
        for application_delay in (5, 23_537):
            with self.subTest(application_delay=application_delay):
                await self._run_relay(application_delay)

    async def _run_relay(self, delay):
        before, release_app, forwarded = (asyncio.Event() for _ in range(3))
        elapsed = [0]
        scope = {'type': 'http', 'method': 'GET', 'raw_path': b'/api/v1/events',
                 'query_string': b'private-marker', 'headers': [(b'cookie', b'private')]}
        timings = timing.GraphResponseTimings()
        relay, browser = [], []
        headers = {'type': 'http.response.start', 'status': 200, 'headers': [(b'foo', b'private')]}
        content = {'type': 'http.response.body', 'body': b'private'}

        async def app(scope, receive, send):
            timing.admit(scope)
            before.set()
            await release_app.wait()
            await send(headers)
            await forwarded.wait()
            await send(content)

        async def buffer(message):
            relay.append(message)

        with patch.object(timing, 'monotonic_ns', side_effect=lambda: elapsed[0] * 10**6):
            task = asyncio.create_task(timing.GraphResponseStart(app, timings)(scope, None, buffer))
            await before.wait()
            self.assertEqual(timings.summary()['admitted'], 1)
            self.assertEqual(sum(timings.summary()['stream_headers']), 0)
            elapsed[0] = delay
            release_app.set()
            await asyncio.sleep(0)
            self.assertEqual(browser, [])
            self.assertEqual(relay, [headers])
            self.assertEqual(timings.summary()['stream_headers'],
                             [1,0,0,0,0,0,0] if delay < 50 else [0,0,0,0,0,1,0])
            elapsed[0] += 23_537
            forwarded.set()
            await task
        browser.extend(relay)
        self.assertEqual(browser, [headers, content])
        self.assertNotIn('private', str(timings.summary()))

    async def test_denial_unwind_and_instance_isolation(self):
        a, b = timing.GraphResponseTimings(), timing.GraphResponseTimings()
        scope = {'type':'http','method':'GET','raw_path':b'/api/v1/events'}
        async def denied(scope, receive, send):
            await send({'type':'http.response.start','status':401})
        async def send(_):
            pass
        async def cancelled(scope, receive, send):
            timing.admit(scope)
            raise asyncio.CancelledError
        await timing.GraphResponseStart(denied,a)(dict(scope),None,send)
        self.assertEqual(a.summary(),b.summary())
        with self.assertRaises(asyncio.CancelledError):
            await timing.GraphResponseStart(cancelled,a)(dict(scope),None,send)
        self.assertEqual(a.summary()['ended_without_headers'],1)
        self.assertEqual(a.summary()['admitted'],1)
        self.assertEqual(b.summary()['admitted'],0)

    async def test_counters_and_histogram_saturate_instead_of_wrapping(self):
        timings = timing.GraphResponseTimings()
        maximum = 2**32-1
        timings.admitted = timings.without_headers = timings.non_stream = maximum-1
        timings.histogram = [maximum-1]*7

        async def app(scope, receive, send):
            timing.admit(scope)
            timing.admit(scope)  # Admission is only counted once.
            if receive is not None:
                await send({'type':'http.response.start','status':receive})

        async def send(_):
            pass

        middleware = timing.GraphResponseStart(app,timings)
        scope = {'type':'http','method':'GET','raw_path':b'/api/v1/events'}
        with patch.object(timing,'monotonic_ns',return_value=0):
            for status in (200,200,429,429,None,None):
                await middleware(dict(scope),status,send)
        self.assertEqual(timings.summary(),{
            'admitted':maximum,'ended_without_headers':maximum,
            'non_stream_responses':maximum,'header_upper_ms':[50,250,1000,4000,15000,30000],
            'stream_headers':[maximum]+[maximum-1]*6})
