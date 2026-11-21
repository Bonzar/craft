"""One current synthetic revision for graph and cards; never reads private sources."""
import base64
import json
from pathlib import Path
import tempfile
import time
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource

def build():
    source = Path(__file__).resolve().parents[2] / "graph_viewer/tests/fixtures/independent-source.json"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with TestClient(create_app(None, root, password="synthetic-test-only",
                        source=JsonSnapshotSource(source, root), synthetic_fixture=True)) as client:
            login = client.post("/api/v1/session", json={"password":"synthetic-test-only"}, headers={"origin":"http://testserver"})
            login.raise_for_status()
            for _ in range(100):
                response = client.get("/api/v1/graph")
                if response.status_code != 202: break
                time.sleep(.02)
            assert response.status_code == 200
            manifest = response.json()
            revision = manifest["revision"]
            topics = client.get(manifest["topics_url"])
            topics.raise_for_status()
            cards, artifacts = [], {}
            for kind in ("nodes", "edges"):
                encodings = {}
                for encoding in ("gzip","identity"):
                    with client.stream("GET", manifest[kind]["url"], headers={"Accept-Encoding":encoding}) as binary:
                        binary.raise_for_status()
                        encodings[encoding] = base64.b64encode(b"".join(binary.iter_raw())).decode()
                artifacts[kind+".bin"] = encodings
                for ordinal in range(manifest["counts"][kind]):
                    card = client.get(f"/api/v1/graph/{revision}/{kind}/{ordinal}")
                    if card.status_code == 404: continue
                    card.raise_for_status()
                    cards.append(dict(kind=kind,card=card.json()))
            return dict(manifest=manifest,capabilities=client.get("/api/v1/capabilities").json(),
                        cards=cards,artifacts=artifacts,topics=topics.json())
if __name__ == "__main__":
    print(json.dumps(build(),ensure_ascii=False,separators=(',',':')))
