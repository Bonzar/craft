"""Prepare only original fictional fixture and compare against its Python HTTP reader."""
import json
import tempfile
import time
from pathlib import Path

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.json_snapshot import JsonSnapshotSource


def build():
    source = Path(__file__).resolve().parents[2] / "graph_viewer/tests/fixtures/independent-source.json"
    fixture = json.loads(source.read_text())
    next(item for item in fixture["overviews"] if item["id"] == "root")["versions"][0]["summary"] = (
        "Синтетический сохранённый обзор маршрутов 🧭. "
        "Пример текста из источника, не генерация в интерфейсе."
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "synthetic.json"
        source.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        with TestClient(create_app(None, root, password="synthetic-test-only",
                        source=JsonSnapshotSource(source, root), synthetic_fixture=True)) as client:
            client.post("/api/v1/session", json={"password":"synthetic-test-only"},
                        headers={"origin":"http://testserver"}).raise_for_status()
            for _ in range(100):
                response=client.get("/api/v1/graph")
                if response.status_code != 202: break
                time.sleep(.02)
            response.raise_for_status()
            manifest=response.json()
            revision=manifest["revision"]
            topics=client.get(manifest["topics_url"]).json()
            prefix="/api/v1/overviews/"
            query=f"?revision={revision}"
            requests=[prefix+"topic%3Aroot"+query+suffix for suffix in (
                "", "&limit=12", "&offset=43&limit=7", "&offset=200", "&limit=0", "&limit=no")]
            requests.extend([prefix+"topic%3Arules"+query,prefix+"topic%3Amissing"+query,
                             prefix+"topic%3Aroot",prefix+"topic%3Aroot?revision=missing"])
            expected=[]
            for path in requests:
                result=client.get(path)
                expected.append([path,result.status_code,result.json()])
            return dict(manifest=manifest,topics=topics,overviews=[expected[0][2]],expected=expected)


if __name__=="__main__":
    print(json.dumps(build(), ensure_ascii=False))
