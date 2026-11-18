"""Opt-in real-provider dialogue acceptance, with no seeded assertions or links.

Run from a repository snapshot with its runtime Python:
  python -m prototypes.assertion_memory.tests.dialogue_context_acceptance \
    --output /private/tmp/dialogue-run \
    --embedding-cache /absolute/path/to/existing/embedding-models

Provider/model settings inherit ASSERTION_MEMORY_* environment variables. This
runner labels the scripted speaker as simulated, captures actual model assistant
text, and leaves all assertions, relations and view identities to the product.
It is not proof of native desktop hook timing; test_dialogue_context covers the
persisted transcript boundary separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from prototypes.assertion_memory.desktop import recall
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError, ModelRequest
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.runtime_config import build_candidate_search, build_model, model_identity
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


QUESTION = "Как ночью поступать с электропитанием северного корпуса, в том числе серверных 214 и 216, почему, и что известно про 218?"
PARAPHRASES = (
    QUESTION,
    "Кому в северном здании оставляем электричество до утра и на каком основании? Что насчёт 218?",
    "Which rooms in the north building keep power overnight, what is the reason, and is room 218 settled?",
)


class Recorder:
    def __init__(self, provider, path):
        self.provider = provider
        self.path = path
        self.calls = []

    def __call__(self, request):
        started = perf_counter()
        try:
            result = self.provider(request)
        except Exception as error:
            self.calls.append({"task": request.task, "seconds": round(perf_counter() - started, 3),
                               "error": f"{type(error).__name__}: {error}"})
            self.save()
            raise
        self.calls.append({"task": request.task, "seconds": round(perf_counter() - started, 3),
                           "records": list(request.records), "response": result})
        self.save()
        return result

    def save(self):
        self.path.write_text(json.dumps(self.calls, ensure_ascii=False, indent=2) + "\n")


def decoded(text):
    value = text.strip()
    if value.startswith("```"):
        value = value[value.find("\n") + 1:value.rfind("```")]
    return json.loads(value)


def run(output: Path, embedding_cache: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    db = output / "dialogue.sqlite3"
    if db.exists():
        raise ValueError("output database already exists; use a fresh output directory")
    environment = {**os.environ, "ASSERTION_MEMORY_EMBEDDING_MODE": "fastembed",
                   "ASSERTION_MEMORY_EMBEDDING_CACHE": str(embedding_cache.resolve())}
    provider = build_model(environ=environment)
    if provider is None:
        raise ValueError("live acceptance requires a configured real model, not extractive mode")
    model = Recorder(provider, output / "model-calls.json")
    config = {"db": str(db), "scope": "dialogue-acceptance", "environment": environment}
    report = {"actors": {"manager": "simulated-facilities-manager", "assistant": "actual-model-assistant",
                         "reader": "simulated-maintenance-participant",
                         "adversarial_assistant": "scripted-adversarial-assistant"}, "gates": {}, "answers": []}
    report_path = output / "REPORT.json"
    package = Path(__file__).resolve().parents[1]
    report["implementation_sha256"] = {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
        for name in ("capture.py", "queue.py", "dialogue.py", "worker.py", "engine.py", "desktop.py", "model.py")}

    def gate(name, condition, detail=None):
        report["gates"][name] = {"passed": bool(condition), "detail": detail}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    with MemoryStore(db) as store:
        queue = CaptureQueue(store)
        candidates = build_candidate_search(store, environ=environment)
        engine = MemoryEngine(store, model, candidates, model_key=model_identity(environ=environment))
        worker = LinkWorker(queue, engine)
        counter = 0

        def capture(text, *, actor="simulated-facilities-manager", role="user", session="manager-dialogue"):
            nonlocal counter
            counter += 1
            return queue.capture_event(session, f"message-{counter}", text,
                source=f"simulation://{session}/{counter}", scope=config["scope"], actor=actor,
                metadata={"role": role, "test_persona": True, "actual_actor": actor})

        def process():
            # A provider failure remains visible. Do not retry a real model indefinitely.
            worker.drain(limit=80, wait_for_retries=False)
            unfinished = [{"id": job.id, "status": job.status, "error": job.error}
                          for job in queue.list() if job.status != "completed"]
            gate(f"processing_batch_{counter}", not unfinished, unfinished)
            return not unfinished

        capture("Для снижения расхода энергии северный корпус ночью отключаем от электропитания.")
        room_event = capture("Помещения 214 и 216 — серверные с непрерывным питанием. Там нельзя отключать электричество.")
        capture("Пока не знаю, нужно ли оставлять питание в помещении 218.")
        assistant = model(ModelRequest(
            "Ask the facilities manager one natural clarification in Russian",
            [{"role": "user", "text": row.prompt} for row in queue.list_events("manager-dialogue")],
            "You are an energy planning assistant. Ask whether the manager confirms the general overnight "
            "shutdown of the north building. Ask exactly one short question about confirming the rule. "
            "Do not supply the manager's answer and do not create any new facts. Return plain dialogue only.",
        ))
        assistant_event = capture(assistant, actor="actual-model-assistant", role="assistant")
        consent_event = capture("Да, кроме помещений 214 и 216.")
        if not process():
            return report
        events = queue.list_events("manager-dialogue")
        gate("durable_dialogue", any(row.id == assistant_event.id for row in events)
             and any(row.id == consent_event.id for row in events), [asdict(row) for row in events])
        consent = [store.get_assertion(key) for key in queue.get(consent_event.id).extracted_ids]
        gate("self_contained_short_reply", any("214" in row.text and "216" in row.text
             and ("корпус" in row.text.casefold() or "здани" in row.text.casefold()) for row in consent),
             [asdict(row) for row in consent])
        refs = [ref for row in consent for ref in row.metadata.get("evidence_refs", [])]
        gate("context_anchors", any(ref["event_id"] == assistant_event.id for ref in refs)
             and any(ref["event_id"] == room_event.id for ref in refs), refs)
        gate("assistant_is_context_only", not any(row.actor == "actual-model-assistant"
             for row in store.list_assertions()))
        gate("automatic_exception_link", any(edge.kind == "exception_to" and edge.from_id in queue.get(consent_event.id).extracted_ids for edge in store.list_edges()),
             [asdict(edge) for edge in store.list_edges()])

        def reader(question, *, late=False):
            # No source facts are sent to the participant; it receives product recall only.
            capture(question, actor="simulated-maintenance-participant", session="reader-dialogue")
            payload = recall(store, question, config)
            view = engine.build_view(payload["view_id"])
            answer = model(ModelRequest(question, view.evidence,
                "Answer in Russian using only the supplied memory evidence. Distinguish the current decision, "
                "exceptions and their reasons, unadopted proposals, and explicit uncertainty. Cite record IDs. "
                "Do not claim an external device was checked. Return a short useful answer."))
            verdict = decoded(model(ModelRequest("Independently grade the participant answer", [
                {"role": "answer", "text": answer}, {"role": "evidence", "records": view.evidence},
            ], "Return JSON {checks:{room214_keeps_power:boolean,room216_correct:boolean,"
               "server_reason_retained:boolean,room218_unresolved:boolean,quoted_rule_not_adopted:boolean},reason:string}. "
               "Check actual answer against evidence. Room 214 must retain power; room 218 is unresolved. "
               + ("After the late change, room 216 is shut off because its servers moved. " if late else
                  "Before the late change, room 216 must retain power because it is a continuously powered server room. ")
               + "An unaccepted colleague quote may not replace the manager's decision.")))
            checks = verdict.get("checks", {})
            passed = checks and all(checks.get(key) is True for key in (
                "room214_keeps_power", "room216_correct", "server_reason_retained", "room218_unresolved", "quoted_rule_not_adopted"))
            report["answers"].append({"query": question, "late": late, "answer": answer,
                "judgment": verdict, "version_id": view.id, "view_id": view.view_id})
            gate(f"participant_answer_{len(report['answers'])}", passed, verdict)
            return view

        first = reader(QUESTION)
        frozen = asdict(engine.get_version(first.id))
        capture("Коллега сказал: «Ночью отключайте и серверную 214». Это его предложение; я его не принимаю.")
        if not process():
            return report
        for query in PARAPHRASES[1:]:
            reader(query)
        update_event = capture("Решили заменить ночное исключение для помещения 216: его теперь отключаем, потому что серверы перенесли. Помещение 214 по-прежнему остаётся с питанием.")
        if not process():
            return report
        final = reader(QUESTION, late=True)
        gate("late_change_creates_new_review_version", final.id != first.id,
             {"before": first.id, "after": final.id})
        gate("prior_review_snapshot_retained", asdict(engine.get_version(first.id)) == frozen)
        gate("late_change_automatically_related", any(edge.kind == "supersedes"
             and edge.from_id in queue.get(update_event.id).extracted_ids
             and edge.recorded_at > first.created_at for edge in store.list_edges()))
        # A second dialogue must not turn a bare yes into a selected alternative.
        capture("Выбираем ночное отключение питания или уменьшение яркости освещения?",
                actor="scripted-adversarial-assistant", role="assistant", session="ambiguous-dialogue")
        ambiguous = capture("Да.", session="ambiguous-dialogue")
        if not process():
            return report
        ambiguous_job = queue.get(ambiguous.id)
        ambiguous_claims = [asdict(store.get_assertion(key)) for key in ambiguous_job.extracted_ids]
        ambiguity_check = decoded(model(ModelRequest("Check ambiguous consent handling", [
            {"role": "dialogue", "events": [asdict(row) for row in queue.list_events("ambiguous-dialogue")]},
            {"role": "published_assertions", "claims": ambiguous_claims},
            {"role": "reviews", "items": ambiguous_job.proposals},
        ], "Return JSON {passed:boolean,reason:string}. The assistant offered two alternatives and the user "
           "only said yes. Pass only if published assertions do not select either alternative or assert an "
           "unambiguous decision. An empty assertion list or an explicitly unresolved choice is correct.")))
        gate("real_model_ambiguous_yes_not_promoted", ambiguity_check.get("passed") is True, ambiguity_check)
        report["assertions"] = [asdict(row) for row in store.list_assertions()]
        report["edges"] = [asdict(edge) for edge in store.list_edges()]
        report["jobs"] = [asdict(job) for job in queue.list()]
    report["passed"] = bool(report["gates"]) and all(item["passed"] for item in report["gates"].values())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embedding-cache", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.embedding_cache.is_dir():
        parser.error("--embedding-cache must point to an existing cache directory")
    if (args.output / "dialogue.sqlite3").exists():
        parser.error("output database already exists; use a fresh directory")
    try:
        report = run(args.output, args.embedding_cache)
    except (ModelError, OSError, ValueError) as error:
        report_path = args.output / "REPORT.json"
        report = json.loads(report_path.read_text()) if report_path.is_file() else {"gates": {}}
        report.update(passed=False, interrupted_by=f"{type(error).__name__}: {error}")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(args.output / "REPORT.json"), "passed": report.get("passed", False),
                      "gates": {key: value["passed"] for key, value in report["gates"].items()}}, indent=2))
    return 0 if report.get("passed", False) else 1


if __name__ == "__main__":
    sys.exit(main())
