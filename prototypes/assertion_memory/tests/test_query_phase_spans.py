"""Verify real span encoding, with synthetic data and a non-network exporter."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import genai_traces, query_timing
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.query_snapshot import QuerySnapshot
from prototypes.assertion_memory.thought_answering import _check_sufficiency
from prototypes.assertion_memory.thought_navigation import neighborhood


class QueryPhaseSpans(unittest.TestCase):
    def test_correlated_nested_ok_failure_and_no_payload(self):
        fixture = QuerySnapshot(0,'synthetic date',0,0,0,None,None,'fixture','fixture',{},[],())
        private = 'DO_NOT_EXPORT_837_fixture'
        reply = {'verdict':'ready','question':None,'text':private}
        engine = SimpleNamespace(_call=lambda *_: reply)
        wire = []
        journal = []
        trace_id = 'a'*32
        with patch.object(genai_traces,'_exporter',SimpleNamespace(put=wire.append)), \
             patch.object(query_timing,'_write_line',side_effect=journal.append), \
             patch('prototypes.assertion_memory.thought_navigation._admit',return_value=private):
            fixture.relation_gaps()
            self.assertEqual(wire, [])
            with query_timing.bind(trace_id=trace_id), query_timing.span('query_total'):
                self.assertIs(_check_sufficiency(engine,[private],[],private,exact=True), reply)
                self.assertIn('source_frontier_sha256',fixture.relation_gaps([private]))
                with self.assertRaises(ModelError):
                    neighborhood(engine,object(),private)
        picked = [s for s in wire if s['name']!='memory_query.query_total']
        self.assertEqual(len(wire),4)
        root = wire[-1]
        self.assertTrue(all(s['traceId']==trace_id for s in wire))
        self.assertTrue(all(s['parentSpanId']==root['spanId'] for s in picked))
        self.assertTrue(all(int(s['endTimeUnixNano']) >= int(s['startTimeUnixNano']) for s in wire))
        self.assertNotIn(private,json.dumps(wire) + b''.join(journal).decode())
        statuses={item['stage']:item['status'] for item in map(json.loads,journal)}
        self.assertEqual({k:statuses[k] for k in ('sufficiency','relation_frontier','relation_neighborhood')},
                         {'sufficiency':'ok','relation_frontier':'ok','relation_neighborhood':'failed'})
