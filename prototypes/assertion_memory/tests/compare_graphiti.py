"""Run Graphiti and the assertion-memory reference on identical synthetic statements.

Optional dependencies: graphiti-core[kuzu]==0.31.2. Only the checked-in synthetic
fixture is submitted to the explicitly configured model and embedding providers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from ..capture import CaptureInput
from ..engine import MemoryEngine, ViewSpec
from ..model import CandidateSearch, LocalOpenAIEmbedder, ModelRequest, SubprocessModel
from ..queue import CaptureQueue
from ..storage import MemoryStore
from ..worker import LinkWorker
from .benchmark import FIXTURE, QUERIES, run


def _json_response(text):
    text = text.strip()
    if text.startswith("```") and text.endswith("```") and "\n" in text:
        text = text.split("\n", 1)[1][:-3].strip()
    return json.loads(text)


def _source(record):
    return json.dumps({
        "source": record["source"], "episode": record["episode"], "actor": record["actor"],
        "recorded_at": record["recorded_at"], "valid_at": record.get("valid_at"),
        "excerpt": record.get("metadata", {}).get("excerpt"),
    }, ensure_ascii=False, sort_keys=True)


async def graphiti_run(path, corpus, model, embedder, limit):
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.client import CrossEncoderClient
    from graphiti_core.driver.kuzu_driver import KuzuDriver
    from graphiti_core.embedder.client import EmbedderClient
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.nodes import EpisodicNode, EpisodeType
    from graphiti_core.search.search_config import EpisodeReranker, EpisodeSearchConfig, EpisodeSearchMethod, SearchConfig

    class LLMAdapter(LLMClient):
        def __init__(self):
            super().__init__(None)
            self.calls = 0

        async def _generate_response(self, messages, response_model=None, *_, **_options):
            self.calls += 1
            instructions = "\n\n".join(f"{message.role}: {message.content}" for message in messages)
            if response_model is not None:
                instructions += "\n\nJSON schema:\n" + json.dumps(response_model.model_json_schema())
            inputs = [{"id": f"graphiti-input-{index}", "text": message.content}
                      for index, message in enumerate(messages) if message.role != "system"]
            output = await asyncio.to_thread(model, ModelRequest(
                "Perform the Graphiti extraction specified in the instructions on the supplied inputs. "
                "Return only the JSON object matching the response schema.", inputs, instructions,
            ))
            result = _json_response(output)
            if response_model is not None:
                return response_model.model_validate(result).model_dump(mode="json")
            if not isinstance(result, dict):
                raise ValueError("Graphiti response must be a JSON object")
            return result

    class EmbedderAdapter(EmbedderClient):
        async def create(self, input_data):
            items = [input_data] if isinstance(input_data, str) else list(input_data)
            if len(items) != 1 or not isinstance(items[0], str):
                raise ValueError("This comparison uses string embeddings")
            return (await self.create_batch(items))[0]

        async def create_batch(self, input_data_list):
            return [list(vector) for vector in await asyncio.to_thread(embedder, input_data_list)]

    class RerankerAdapter(CrossEncoderClient):
        async def rank(self, query, passages):
            matches = await asyncio.to_thread(
                CandidateSearch(embedder).rank, query, dict(enumerate(passages)), limit=len(passages),
            )
            return [(passages[hit.id], hit.score) for hit in matches]

    adapter = LLMAdapter()
    graph = Graphiti(
        graph_driver=KuzuDriver(str(path)), llm_client=adapter,
        embedder=EmbedderAdapter(), cross_encoder=RerankerAdapter(),
    )
    ids = {}
    input_records = {record["assertion_id"]: record for record in corpus["assertions"]}
    ingestion = []
    try:
        # The deprecated Kuzu driver's general index initializer is a no-op.
        await graph.driver.graph_ops.build_indices_and_constraints(graph.driver)
        for key, record in sorted(input_records.items(), key=lambda pair: pair[1]["recorded_at"]):
            started = perf_counter()
            before = adapter.calls
            result = await graph.add_episode(
                name=key, episode_body=record["text"], source_description=_source(record),
                source=EpisodeType.text,
                reference_time=datetime.fromisoformat(
                    (record.get("valid_at") or record["recorded_at"]).replace("Z", "+00:00")
                ),
                previous_episode_uuids=[],
            )
            ids[result.episode.uuid] = key
            ingestion.append({"id": key, "ms": round((perf_counter() - started) * 1000, 3),
                              "model_calls": adapter.calls - before, "edges": len(result.edges)})
            print(f"Graphiti ingested {key}: {len(result.nodes)} nodes, {len(result.edges)} edges",
                  file=sys.stderr, flush=True)
        episodes = await EpisodicNode.get_by_uuids(graph.driver, list(ids))
        intact_sources = sorted(
            ids[item.uuid] for item in episodes
            if item.content == input_records[ids[item.uuid]]["text"]
            and item.source_description == _source(input_records[ids[item.uuid]])
        )
        results = []
        episodes_config = SearchConfig(episode_config=EpisodeSearchConfig(
            search_methods=[EpisodeSearchMethod.bm25], reranker=EpisodeReranker.rrf,
        ), limit=limit)
        for name, query, targets, expected in QUERIES:
            started = perf_counter()
            matches = await graph.search(query, num_results=limit)
            duration = round((perf_counter() - started) * 1000, 3)
            ranked_ids = list(dict.fromkeys(ids.get(uuid, uuid) for edge in matches for uuid in edge.episodes))[:limit]
            episode_matches = await graph.search_(query, config=episodes_config)
            raw_episode_ids = [ids.get(episode.uuid, episode.uuid) for episode in episode_matches.episodes]
            results.append({
                "case": name, "ms": duration, "gold_ids": sorted(targets), "ranked_ids": ranked_ids,
                "ranked_recall": len(targets.intersection(ranked_ids)) / len(targets),
                "raw_episode_ids": raw_episode_ids,
                "raw_episode_recall": len(targets.intersection(raw_episode_ids)) / len(targets),
                "expected_dispositions": expected,
                "facts": [{
                    "text": edge.fact, "kind": edge.name,
                    "source_ids": [ids.get(uuid, uuid) for uuid in edge.episodes],
                    "valid_at": edge.valid_at, "invalid_at": edge.invalid_at,
                    "created_at": edge.created_at, "expired_at": edge.expired_at,
                } for edge in matches],
            })
    finally:
        await graph.close()
    return {
        "version": version("graphiti-core"), "ingestion": ingestion,
        "intact_sources": intact_sources, "model_calls": adapter.calls,
        "mean_ranked_recall": sum(row["ranked_recall"] for row in results) / len(results),
        "mean_raw_episode_recall": sum(row["raw_episode_recall"] for row in results) / len(results),
        "cases": results,
    }


def auto_link(path, corpus, model, candidates, limit):
    checks = []
    with MemoryStore(path) as store:
        engine = MemoryEngine(store, candidate_search=candidates)
        queue = CaptureQueue(store)
        worker = LinkWorker(queue, engine, model, candidate_limit=limit)
        for record in sorted(corpus["assertions"], key=lambda item: item["recorded_at"]):
            started = perf_counter()
            assertion = queue.capture(CaptureInput(**record))
            worker.drain(limit=1)
            job = queue.get(assertion.assertion_id)
            checks.append({
                "id": assertion.assertion_id, "ms": round((perf_counter() - started) * 1000, 3),
                "status": job.status, "error": job.error, "candidates": job.candidates,
                "proposals": job.proposals,
            })
            print(f"Assertion memory processed {assertion.assertion_id}", file=sys.stderr, flush=True)
        cases = []
        for name, query, targets, expected in QUERIES:
            engine.define_view(ViewSpec(name, query, query, max_seeds=limit))
            view = engine.build_view(name)
            evidence = {record["id"]: record["disposition"] for record in view.evidence}
            cases.append({
                "case": name, "gold_ids": sorted(targets), "evidence": evidence,
                "expected_dispositions": expected, "observed_dispositions": {
                    item: evidence.get(item) for item in expected
                },
            })
        return {
            "ingestion": checks, "cases": cases,
            "edges": [asdict(edge) for edge in store.list_edges()],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-endpoint", default="http://127.0.0.1:11434/v1/embeddings")
    parser.add_argument("--model-command", help="JSON argv array of a model reading stdin")
    parser.add_argument("--runtime", action="store_true", help="use the production provider configuration")
    parser.add_argument("--engine", choices=("both", "graphiti", "prototype"), default="both")
    parser.add_argument("--explicit-revision", action="store_true", help="use a clear decision superseding the old default")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("limit must be positive")
    if not args.runtime and (not args.embedding_model or not args.model_command):
        parser.error("choose --runtime or specify --embedding-model and --model-command")
    os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if args.explicit_revision:
        revised = next(record for record in corpus["assertions"] if record["assertion_id"] == "clone-ui-version-revised")
        revised["text"] = "Решили: вместо опубликованной версии форма клонирования предвыбирает черновик."
        revised["metadata"]["excerpt"] = revised["text"]
    with tempfile.TemporaryDirectory(prefix="graphiti-acceptance-") as temp:
        root = Path(temp)
        with MemoryStore(root / "embedding-cache.sqlite3") as cache:
            if args.runtime:
                from ..runtime_config import build_candidate_search, build_model

                model = build_model()
                candidates = build_candidate_search(cache)
                embedder = getattr(candidates.embedder, "backend", candidates.embedder)
            else:
                model = SubprocessModel(json.loads(args.model_command))
                embedder = LocalOpenAIEmbedder(args.embedding_endpoint, args.embedding_model)
                candidates = CandidateSearch(embedder)
            if model is None or embedder is None:
                parser.error("comparison requires a real model and embedding provider")
            report = {}
            if args.engine in {"both", "graphiti"}:
                report["graphiti_automatically_linked"] = asyncio.run(
                    graphiti_run(root / "graphiti.kuzu", corpus, model, embedder, args.limit)
                )
            if args.engine in {"both", "prototype"}:
                report["assertion_memory_automatically_linked"] = auto_link(
                    root / "prototype.sqlite3", corpus, model, candidates, args.limit
                )
                if not args.explicit_revision:
                    report["assertion_memory_with_fixture_links"] = run(
                        root / "reference.sqlite3", len(corpus["assertions"]), candidates, None, args.limit
                    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
