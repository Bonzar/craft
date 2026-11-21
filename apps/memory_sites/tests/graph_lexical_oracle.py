"""Compare Worker SQL with original search() on the synthetic JSON projection."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource
from apps.graph_viewer.server.search import search

def run(probes):
    fixture=Path(__file__).resolve().parents[2]/"graph_viewer/tests/fixtures/independent-source.json"
    result={"actual":[],"expected":[]}
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)
        app=create_app(None,root,password="synthetic-test-only",source=JsonSnapshotSource(fixture,root))
        with TestClient(app) as client:
            client.post("/api/v1/session",json={"password":"synthetic-test-only"},headers={"origin":"http://testserver"}).raise_for_status()
            import time
            for probe in probes:
                for _ in range(100):
                    response=client.get("/api/v1/graph",params={"history":int(probe["history"])})
                    if response.status_code!=202: break
                    time.sleep(.02)
                response.raise_for_status()
                graph=app.state.manager.get(response.json()["revision"])
                expected=search(graph,{"q":probe["query"],"limit":"200"})
                if expected["topic_hits"] or expected["next_cursor"]:
                    raise AssertionError("Differential probe must isolate graph-node search")
                compiled=probe.get("compiled")
                with graph.connect() as conn:
                    rows=conn.execute(compiled["sql"],compiled["values"]).fetchall() if compiled else []
                result["actual"].append([[r["id"],r["ordinal"],-r["score"]] for r in rows])
                result["expected"].append([[h["id"],h["ordinal"],h["score"]] for h in expected["hits"]])
    return result

if __name__=="__main__":
    print(json.dumps(run(json.load(sys.stdin))))
