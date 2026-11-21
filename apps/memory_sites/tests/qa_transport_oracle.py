"""Generate a golden from the actual original Python API using only its synthetic fixture."""
import base64
import json
from pathlib import Path
import tempfile
import time

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource


def golden():
    fixture = Path(__file__).resolve().parents[2] / "graph_viewer/tests/fixtures/independent-source.json"
    with tempfile.TemporaryDirectory() as directory:
        host = Path(directory)
        source = JsonSnapshotSource(fixture, host)
        with TestClient(create_app(None, host, password="synthetic-test-only", source=source)) as client:
            unauthorized = client.get("/api/v1/graph")
            login = client.post("/api/v1/session", json={"password":"synthetic-test-only"}, headers={"origin":"http://testserver"})
            assert login.status_code == 200
            for _ in range(100):
                graph = client.get("/api/v1/graph")
                if graph.status_code != 202:
                    break
                time.sleep(.025)
            graph.raise_for_status()
            manifest = graph.json()
            result = dict(manifest=manifest, etag=graph.headers["etag"],
                          capabilities=client.get("/api/v1/capabilities").json(),
                          unauthorized=unauthorized.json(),
                          expired=client.get("/api/v1/graph/invalid/nodes.bin").json(),
                          artifacts={})
            for kind in ("nodes", "edges"):
                encoding_records = {}
                for encoding in ("identity", "gzip"):
                    with client.stream("GET",manifest[kind]["url"],headers={"Accept-Encoding":encoding}) as response:
                        response.raise_for_status()
                        raw = b"".join(response.iter_raw())
                        headers = {key:response.headers[key] for key in ("etag","vary","content-encoding","content-type","content-length","cache-control")}
                        encoding_records[encoding] = dict(base64=base64.b64encode(raw).decode(),headers=headers)
                result["artifacts"][kind + ".bin"] = encoding_records
    return result


if __name__ == "__main__":
    print(json.dumps(golden()))
