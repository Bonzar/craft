"""Process-local aggregate of authenticated graph-v1 ASGI response starts."""
from time import monotonic_ns

_MARK = 'assertion.graph_v1_response_timing'
_LIMITS = (50, 250, 1000, 4000, 15000, 30000)
_MAX = 2**32 - 1


def admit(scope):
    mark = scope.get(_MARK)
    if mark is not None:
        mark()


class GraphResponseTimings:
    def __init__(self):
        self.admitted = self.without_headers = self.non_stream = 0
        self.histogram = [0] * (len(_LIMITS) + 1)

    def summary(self):
        return {'admitted': self.admitted,
                'ended_without_headers': self.without_headers,
                'non_stream_responses': self.non_stream,
                'header_upper_ms': list(_LIMITS),
                'stream_headers': list(self.histogram)}


class GraphResponseStart:
    def __init__(self, app, timings):
        self.app = app
        self.timings = timings

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or scope.get('method') != 'GET'
                or scope.get('raw_path') != b'/api/v1/events'):
            return await self.app(scope, receive, send)
        clock = monotonic_ns()
        admitted = started = False

        def mark():
            nonlocal admitted
            if not admitted:
                admitted = True
                self.timings.admitted = min(_MAX, self.timings.admitted + 1)

        async def observed(message):
            nonlocal started
            is_start = admitted and not started and message['type'] == 'http.response.start'
            elapsed = (monotonic_ns() - clock) // 1_000_000 if is_start else 0
            await send(message)
            if is_start:
                started = True
                if message.get('status') == 200:
                    bucket = next((i for i, upper in enumerate(_LIMITS) if elapsed <= upper), len(_LIMITS))
                    histogram = self.timings.histogram
                    histogram[bucket] = min(_MAX, histogram[bucket] + 1)
                else:
                    self.timings.non_stream = min(_MAX, self.timings.non_stream + 1)

        scope[_MARK] = mark
        try:
            await self.app(scope, receive, observed)
        finally:
            scope.pop(_MARK, None)
            if admitted and not started:
                self.timings.without_headers = min(_MAX, self.timings.without_headers + 1)
