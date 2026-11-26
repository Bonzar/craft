"""Bounded, redacted SSE witness; use only with an already authorized session."""
from argparse import ArgumentParser
import http.client
import json
import re
import signal
import ssl
import sys
import time
from urllib.parse import urlsplit


def witness(conn, cookie):
    start = time.monotonic()
    timings = {}
    report = {}
    try:
        headers = {"Accept": "text/event-stream", "Cache-Control":"no-cache"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request("GET", "/api/v1/events", headers=headers)
        rsp = conn.getresponse()
        report["status"] = rsp.status
        report["header_ms"] = round((time.monotonic() - start) * 1000)
        report["event_stream"] = rsp.headers.get_content_type() == "text/event-stream"
        if rsp.status != 200 or not report["event_stream"]:
            return report
        event = None
        for _ in range(256):
            line = rsp.readline(8193)
            if not line:
                break
            if len(line) > 8192:
                report["limit"] = True
                break
            if line in (b"\n", b"\r\n"):
                t = round((time.monotonic() - start) * 1000)
                if event is not None and event not in timings:
                    timings[event] = t
                event = None
                if "hello" in timings and "heartbeat" in timings:
                    break
            match = re.fullmatch(br"event: (hello|heartbeat|reset)\r?\n", line)
            if match:
                event = match[1].decode("ascii")
    except (TimeoutError, http.client.HTTPException, OSError) as exc:
        report["stopped"] = type(exc).__name__
    finally:
        conn.close()
    report["elapsed_ms_by_event_type"] = timings
    first, second = timings.get("hello"), timings.get("heartbeat")
    report["distinct_live_delivery"] = (first is not None and second is not None
                                         and first < 4000 and 1000 < second-first < 11000)
    return report


def main():
    parser = ArgumentParser()
    parser.add_argument("https_host")
    parser.add_argument("--session-from-stdin", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9]+\.[a-z0-9]+\.apigw\.yandexcloud\.net",args.https_host):
        raise SystemExit("Refused non-managed host")
    cookie = None
    if args.session_from_stdin:
        if sys.stdin.isatty():
            raise SystemExit("Credential input must not be a terminal")
        cookie = sys.stdin.buffer.readline(4097).rstrip(b"\n")
        if not re.fullmatch(rb"[^\x00-\x20\x7f-\xff]{2,4096}",cookie):
            raise SystemExit("Refused malformed session input")
        cookie = cookie.decode("ascii")
    def expired(*_):
        raise TimeoutError()
    signal.signal(signal.SIGALRM,expired)
    signal.alarm(15)
    try:
        report = witness(http.client.HTTPSConnection(args.https_host,timeout=13,
                           context=ssl.create_default_context()),cookie)
        print(json.dumps(report,sort_keys=True))
    finally:
        signal.alarm(0)

if __name__ == "__main__":
    main()
