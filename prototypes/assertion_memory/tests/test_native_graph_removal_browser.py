"""The ordinary native owner Web API retires only one confirmed TEST thought."""
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient

from apps.graph_viewer.server import queue_dashboard
from apps.graph_viewer.server.auth import COOKIE, OwnerStore
from prototypes.assertion_memory import base_browser_surface as surface
from prototypes.assertion_memory.dashboard_projection import projection_path
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_graph import export_graph
from prototypes.assertion_memory.thought_runtime import initialize_empty
from prototypes.assertion_memory.thought_store import ThoughtStore


class NativeGraphRemovalBrowser(TestCase):
    def test_existing_owner_confirms_exact_test_thought_and_other_lane_stays_dark(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        identity = lambda: ("fixture-release", "a" * 64)
        clients, origins, databases, nodes_by_stage = {}, {}, {}, {}
        for stage in ("test", "prod"):
            path = root / stage / "state" / "fixture.sqlite3"
            path.parent.mkdir(parents=True)
            databases[stage] = path
            config = ServiceConfig(db=str(path), provider="none", processing_session_ids=[])
            with MemoryStore(path) as store:
                initialize_empty(store)
                MemoryService(store).capture_message("fictional", "m", "qa", "user", "Kept original", 1, "fixture")
                thoughts = ThoughtStore(store)
                target = thoughts.publish("Fictional thought for confirmed deletion")
                survivor = thoughts.publish("Unrelated fictional thought")
                graph = export_graph(thoughts)
                ids = {node["thought_ref"]["thought_id"]: node["id"] for node in graph["nodes"]}
                nodes_by_stage[stage] = target.id, target.version, ids[target.id], ids[survivor.id], graph["source_revision"]
            owner = OwnerStore(path.parent / "graph-state", "synthetic only")
            cookie, _ = owner.login("synthetic only", stage)
            profile = surface.LANES[stage]
            lane = replace(profile, base=replace(profile.base, root=root/stage, db=path))
            dashboard = None
            with (patch.object(surface, "LANES", dict(surface.LANES) | {stage: lane}),
                  patch.dict(queue_dashboard.SCOPES, {"test": ("assertion-test-deploy", path)}),
                  patch("pwd.getpwnam", return_value=SimpleNamespace(pw_uid=994))):
                if stage == "test":
                    dashboard = queue_dashboard.HostedQueueDashboard(
                        "test", projection_path("test"), path, activation=lambda: ("a"*40, "b"*64))
                    dashboard.release_lease = nullcontext
                    dashboard.metadata = lambda: {"release_version": identity()[0], "generation": identity()[1]}
                app = surface.base_browser_app(stage, config=config, private_port=lane.private_port,
                    verified_identity=identity, release_lease=nullcontext, interactive=False, test_dashboard=dashboard)
            client = self.enterContext(TestClient(app, base_url=lane.origin))
            self.assertNotIn("owner_graph_node_removal", client.get("/api/v1/capabilities").json())
            client.cookies.set(COOKIE, cookie)
            clients[stage], origins[stage] = client, lane.origin

        test = clients["test"]
        self.assertEqual(test.get("/api/v1/capabilities").json()["owner_graph_node_removal"],
                         {"version": 1, "stage": "test"})
        prod = clients["prod"]
        self.assertNotIn("owner_graph_node_removal", prod.get("/api/v1/capabilities").json())
        target, version, target_node, survivor_node, revision = nodes_by_stage["test"]
        url = "/api/v1/processing/graph-nodes/remove"
        body = {"stage": "test", "thought_id": target, "thought_version": version}
        headers = {"Origin": origins["test"]}
        self.assertEqual(prod.post(url+"/prepare", json=body, headers={"Origin": origins["prod"]}).status_code, 404)
        prep = test.post(url+"/prepare", json=body, headers=headers)
        self.assertEqual(prep.status_code, 200, prep.text)
        proof = prep.json()
        self.assertEqual(test.put(url, json=body | {"expected_revision": proof["revision"]}, headers=headers).status_code, 403)
        with MemoryStore(databases["test"]) as store:
            self.assertEqual(export_graph(ThoughtStore(store))["source_revision"], revision)

        receipt = test.put(url, json=body | {"expected_revision": proof["revision"]},
                           headers=headers | {"X-CSRF-Token": proof["csrf_token"]})
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.assertTrue(receipt.json()["removed"])
        self.assertNotEqual(receipt.json()["source_revision"], revision)
        replay = test.put(url, json=body | {"expected_revision": proof["revision"]},
                          headers=headers | {"X-CSRF-Token": proof["csrf_token"]})
        self.assertIn(replay.status_code, (403, 409))
        with MemoryStore(databases["test"]) as store:
            graph = export_graph(ThoughtStore(store))
            self.assertNotIn(target_node, {node["id"] for node in graph["nodes"]})
            self.assertIn(survivor_node, {node["id"] for node in graph["nodes"]})
            self.assertEqual(graph["source_revision"], receipt.json()["source_revision"])
            with store.transaction() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM memory_raw_events").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM memory_thought_versions WHERE thought_id=?", (target,)).fetchone()[0], 1)
        with MemoryStore(databases["prod"]) as store:
            self.assertEqual(export_graph(ThoughtStore(store))["source_revision"], nodes_by_stage["prod"][-1])
