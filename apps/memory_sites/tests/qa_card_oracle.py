"""Prepare selected immutable card HTTP responses from the original synthetic API."""
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import quote
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource

def collect():
    fixture = Path(__file__).resolve().parents[2] / "graph_viewer/tests/fixtures/independent-source.json"
    with tempfile.TemporaryDirectory() as directory:
        host = Path(directory)
        with TestClient(create_app(None, host, password="synthetic-test-only",
                        source=JsonSnapshotSource(fixture, host))) as client:
            api = "/api/v1"
            visitor = client.get(api+"/session").json()
            denied = client.get(api+"/nodes/old").json()
            login = client.post(api+"/session", json={"password":"synthetic-test-only"},
                                headers={"origin":"http://testserver"})
            login.raise_for_status()
            for _ in range(100):
                r = client.get(api+"/graph?history=1")
                if r.status_code == 200: break
                time.sleep(.02)
            else: raise RuntimeError("Original synthetic graph was not prepared")
            manifest = r.json()
            rev = manifest["revision"]
            samples = []
            for kind in ("nodes","edges"):
                for ordinal in range(manifest["counts"][kind]):
                    path = f"{api}/graph/{rev}/{kind}/{ordinal}"
                    r = client.get(path); r.raise_for_status(); card = r.json()
                    stable = api+"/"+kind+"/"+quote(card["id"],safe="")+"?revision="+rev
                    s = client.get(stable); s.raise_for_status(); assert s.json() == card
                    samples.append(dict(kind=kind,path=path,stable=stable,ordinal=ordinal,card=card))
            errors = []
            for suffix in ("/nodes/old", "/nodes/missing?revision="+rev, "/edges/missing?revision="+rev,
                           "/graph/missing/nodes/0", f"/graph/{rev}/nodes/1000"):
                e = client.get(api+suffix); errors.append(dict(path=api+suffix,status=e.status_code,body=e.json()))
            return dict(revision=rev, visitor=visitor, denied=denied, owner=client.get(api+"/session").json(),
                        samples=samples, errors=errors)

if __name__ == "__main__":
    print(json.dumps(collect()))
