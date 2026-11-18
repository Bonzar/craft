"""Measure search and answer construction on a deterministic synthetic corpus."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from time import perf_counter

from ..engine import MemoryEngine, ViewSpec
from ..model import CandidateSearch, LocalOpenAIEmbedder, SubprocessModel, format_prompt
from ..storage import MemoryStore


FIXTURE = Path(__file__).parent / "fixtures" / "clone_scenarios.json"
DISTRACTORS = (
    "Новая редакция сметы в форме закупки сохраняет опубликованный шаблон",
    "При сбое автоматического платежа сервис возвращает статус операции",
    "Форма копирования отчёта открывается с черновиком прошлого месяца",
    "Перед созданием ярлыка интерфейс проверяет доступность коллекции",
    "При импорте шаблона обработчик читает сохранённый снимок документа",
    "После редактирования карточки приложение показывает ошибку сохранения",
    "Для группировки писем виджет передаёт идентификатор папки",
    "Последняя версия календаря загружается при открытии формы",
    "Журнал аудита фиксирует откат публикации и дату наблюдения",
)
QUERIES = (
    ("collection-exact", "collectionId таблицы сценариев", {"clone-collection"},
     {"clone-collection": "decision", "widget-collection-exception": "exception"}),
    ("collection-paraphrase", "Какое место назначения получает дубль из перечня автоматизаций?",
     {"clone-collection"}, {"clone-collection": "decision", "widget-collection-exception": "exception"}),
    ("exception-paraphrase", "Which folder owns a new copy created from the embedded control?",
     {"widget-collection-exception"}, {"widget-collection-exception": "exception"}),
    ("default-exact", "Что выбрано по умолчанию в форме клонирования сценария?",
     {"clone-ui-version-revised"}, {"clone-ui-version": "superseded",
                                  "clone-ui-version-revised": "decision", "clone-ui-version-proposal": "proposal"}),
    ("default-paraphrase", "Что подставляется при дублировании автоматизации до действия пользователя?",
     {"clone-ui-version-revised"}, {"clone-ui-version-revised": "decision"}),
    ("snapshot-exact", "BFF читает снимок версии или последнее сохранённое состояние?",
     {"clone-bff-source", "clone-bff-dispute"}, {"clone-bff-source": "conflict", "clone-bff-dispute": "conflict"}),
    ("rollback-paraphrase", "What happens to the new workflow if making its first revision fails?",
     {"clone-bff-rollback"}, {"clone-bff-rollback": "decision"}),
)


def load_corpus(store, count):
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with store.transaction(write=True):
        for record in corpus["assertions"]:
            store.append_assertion(**record)
        for index in range(count - len(corpus["assertions"])):
            scope, number = index % len(DISTRACTORS), index // len(DISTRACTORS)
            store.append_assertion(
                assertion_id=f"noise-{index:05}", text=f"{DISTRACTORS[scope]}. Объект {scope}-{number:04}.",
                actor="Fixture", source=f"fixture://distractor/{index}", episode=f"noise-{scope}",
                recorded_at="2026-09-01", scope=f"noise/{scope}",
            )
        for edge in corpus["edges"]:
            store.append_edge(**edge)


def timed(function, *args, **kwargs):
    start = perf_counter()
    result = function(*args, **kwargs)
    return result, round((perf_counter() - start) * 1000, 3)


class MeasureModel:
    def __init__(self, model):
        self.model = model
        self.calls = []

    def __call__(self, request):
        prompt = format_prompt(request)
        result, duration = timed(self.model, request)
        self.calls.append({"ms": duration, "prompt_bytes": len(prompt.encode()), "records": len(request.records)})
        return result


def run(path, count, candidates, model, limit):
    model = MeasureModel(model) if model else None
    results = []
    with MemoryStore(path) as store:
        engine = MemoryEngine(store, model, candidates)
        _, load_ms = timed(load_corpus, store, count)
        corpus = {row.assertion_id: row.text for row in store.list_assertions()}
        for name, query, targets, expected in QUERIES:
            # Build before isolated ranking to preserve the real first query latency.
            engine.define_view(ViewSpec(name, query, query, max_seeds=limit))
            call_count = len(model.calls) if model else 0
            first, first_ms = timed(engine.build_view, name)
            repeated, repeat_ms = timed(engine.build_view, name)
            fts, fts_ms = timed(store.search, query, limit=limit)
            ranking, ranking_ms = timed(candidates.rank, query, corpus, limit=limit)
            first_ids = {row["id"]: row["disposition"] for row in first.evidence}
            fts_ids = [hit.assertion.assertion_id for hit in fts]
            ranked_ids = [hit.id for hit in ranking]
            request = engine.model_request(first)
            prompt = format_prompt(request)
            results.append({
                "case": name, "gold_ids": sorted(targets),
                "fts_ids": fts_ids, "fts_ms": fts_ms,
                "ranked_ids": ranked_ids, "ranking_ms": ranking_ms,
                "fts_recall": len(targets.intersection(fts_ids)) / len(targets),
                "ranked_recall": len(targets.intersection(ranked_ids)) / len(targets),
                "expected_dispositions": expected, "observed_dispositions": {
                    item: first_ids.get(item) for item in expected
                },
                "evidence_count": len(first.evidence), "prompt_records": len(request.records),
                "prompt_characters": len(prompt), "prompt_bytes": len(prompt.encode()),
                "first_ms": first_ms, "repeat_ms": repeat_ms, "same_snapshot": first.id == repeated.id,
                "generator_calls": model.calls[call_count:] if model else [],
            })
    return {
        "assertions": len(corpus), "load_ms": load_ms, "top_k": limit,
        "mean_fts_recall": sum(row["fts_recall"] for row in results) / len(results),
        "mean_ranked_recall": sum(row["ranked_recall"] for row in results) / len(results),
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=9000)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-endpoint", default="http://127.0.0.1:11434/v1/embeddings")
    parser.add_argument("--model-command", help="JSON argument array for an optional generator on synthetic data")
    parser.add_argument("--runtime", action="store_true", help="use production providers configured by environment")
    parser.add_argument("--no-generate", action="store_true", help="measure retrieval with an extractive response")
    args = parser.parse_args()
    if args.count < len(json.loads(FIXTURE.read_text(encoding="utf-8"))["assertions"]) or args.limit < 1:
        parser.error("count must fit the fixture and limit must be positive")
    with tempfile.TemporaryDirectory(prefix="memory-acceptance-") as temp:
        root = Path(temp)
        with MemoryStore(root / "embedding-cache.sqlite3") as cache:
            if args.runtime:
                from ..runtime_config import build_candidate_search, build_model

                candidates = build_candidate_search(cache)
                model = None if args.no_generate else build_model()
            else:
                embedder = LocalOpenAIEmbedder(args.embedding_endpoint, args.embedding_model) if args.embedding_model else None
                candidates = CandidateSearch(embedder)
                model = SubprocessModel(json.loads(args.model_command)) if args.model_command else None
            result = run(root / "memory.sqlite3", args.count, candidates, model, args.limit)
    print(json.dumps({
        "ranker": candidates.identity, "generator": type(model).__name__ if model else "extractive",
        **result,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
