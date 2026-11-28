"""Optional best-effort mirror of canonical query timing, not journal scraping."""
from http.client import HTTPConnection
import json
from threading import Lock
from time import time_ns

from .genai_traces import _Exporter, _attribute, _PORTS

_SCOPE = 'assertion_memory.query_timing'
_FIELDS = ('event','schema_version','stage','status','trace_id','duration_ms')
_exporter = None
_lock = Lock()


class _LogExporter(_Exporter):
    """Independent instance: never share trace queue, worker or HTTP connection."""
    QUEUE_SIZE = 128
    BATCH_SIZE = 16

    def _send(self, batch):
        resource = {'attributes': [_attribute('service.name', 'assertion-memory-' + self.lane),
                                    _attribute('deployment.environment.name', self.lane)]}
        data = {'resourceLogs':[{'resource':resource,
                'scopeLogs':[{'scope':{'name':_SCOPE},'logRecords':batch}]}]}
        body=json.dumps(data,separators=(',',':'),ensure_ascii=True).encode('ascii')
        conn=HTTPConnection('127.0.0.1',_PORTS[self.lane],timeout=1)
        try:
            conn.request('POST','/v1/logs',body,{'Content-Type':'application/json'})
            with conn.getresponse() as response:
                response.read(4096)
        finally:
            conn.close()


def configure(lane, *, enabled=False):
    """Trusted hosted startup only, identical opt-in contract in either lane."""
    global _exporter
    if enabled is not True or lane not in _PORTS:
        return
    with _lock:
        if _exporter is None:
            try:
                _exporter = _LogExporter(lane)
            except Exception:
                pass


def record(event):
    exporter=_exporter
    if exporter is None:
        return
    try:
        attrs = [_attribute(k,event[k]) for k in _FIELDS if k in event]
        exporter.put({'body':{'stringValue':'memory_query_timing'},
                      'timeUnixNano':str(time_ns()),'attributes':attrs})
    except Exception:
        pass  # Observability never changes the source operation result.
