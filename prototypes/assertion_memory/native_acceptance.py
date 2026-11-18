"""Verify native cross-session recall against persisted turns and hook evidence."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .desktop import event_identity, load_config, native_turn_provenance, transcript_prompts
from .queue import CaptureQueue
from .storage import MemoryStore


def _tail(path: Path) -> list[dict]:
    limit = 2 * 1024 * 1024
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - limit))
        if size > limit:
            stream.readline(limit)
        data = stream.read(limit)
    return [json.loads(line) for line in data.splitlines(keepends=True) if line.endswith(b"\n")]


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _native_turn(event) -> list[dict]:
    identities = {"human": ("user", "codex-desktop"), "native-client": ("codex-client", "codex-native-client")}
    if (event.actor, event.metadata.get("transport")) not in identities.values():
        return []
    path = event.metadata.get("transcript_path")
    if not path:
        return []
    raw = {
        "session_id": event.session_id, "turn_id": event.turn_id, "cwd": event.metadata.get("cwd"),
        "transcript_path": path, "prompt": event.prompt,
    }
    if event_identity(raw)[3] != event.source:
        return []
    provenance = native_turn_provenance(raw)
    if (provenance["status"] != "verified"
            or identities.get(provenance.get("origin_kind")) != (event.actor, event.metadata.get("transport"))):
        return []
    observed = transcript_prompts(raw)
    return _tail(Path(path)) if any(item["prompt"] == event.prompt for item in observed) else []


def _response(rows: list[dict], turn_id: str) -> tuple[str, str] | None:
    active = False
    first_message = None
    for row in rows:
        payload = row.get("payload", {})
        if row.get("type") == "event_msg" and payload.get("type") == "task_started":
            active = payload.get("turn_id") == turn_id
            first_message = None
        if not active:
            continue
        if (row.get("type") == "response_item" and payload.get("type") == "message"
                and payload.get("role") == "assistant" and first_message is None):
            first_message = row.get("timestamp")
        if (row.get("type") == "event_msg" and payload.get("type") == "task_complete"
                and payload.get("turn_id") == turn_id and isinstance(first_message, str)):
            answer = payload.get("last_agent_message")
            return (first_message, answer) if isinstance(answer, str) else None
    return None


def verify_sessions(config: dict, source_session: str, reader_session: str, marker: str) -> dict:
    if not source_session or not reader_session or source_session == reader_session:
        raise ValueError("two different session IDs are required")
    marker = marker.strip().casefold()
    if len(marker) < 8:
        raise ValueError("use a unique marker of at least eight characters")
    path = Path(config["db"])
    if not path.is_file():
        raise ValueError("acceptance database does not exist")
    traces = _tail(path.with_suffix(".desktop.jsonl"))
    scope = config.get("scope", "default")
    with MemoryStore(path) as store:
        queue = CaptureQueue(store)
        sources = {}
        for event in queue.list_events(source_session):
            if event.scope != scope or marker not in event.prompt.casefold():
                continue
            response = _response(_native_turn(event), event.turn_id)
            if response is None or _time(event.recorded_at) >= _time(response[0]):
                continue
            receipts = [trace for trace in traces if trace.get("source") == event.source
                        and trace.get("event") in ("staged", "captured")]
            initial = [trace for trace in receipts if _time(trace["at"]) < _time(response[0])]
            confirmations = [trace for trace in receipts if trace["event"] == "captured"]
            if not initial or not confirmations:
                continue
            first = min(initial, key=lambda trace: _time(trace["at"]))
            confirmed = min(confirmations, key=lambda trace: _time(trace["at"]))
            phase = "verified_before_answer" if first["event"] == "captured" else "staged_before_answer_then_verified"
            job = queue.get(event.id)
            if job is None or job.status != "completed":
                continue
            for assertion_id in job.extracted_ids:
                record = store.get_assertion(assertion_id)
                if (record is None or record.source != event.source or record.actor != event.actor
                        or record.metadata.get("event_id") != event.id or marker not in record.text.casefold()):
                    continue
                span = record.metadata.get("source_span")
                if isinstance(span, dict):
                    start, end = span.get("start"), span.get("end")
                    if (type(start) is int and type(end) is int and 0 <= start < end <= len(event.prompt)
                            and event.prompt[start:end] == record.text):
                        sources[assertion_id] = (event, {
                            "source_capture_phase": phase, "source_durable_at": first["at"],
                            "source_confirmed_at": confirmed["at"], "source_first_answer_at": response[0],
                            "source_extracted_at": record.recorded_at,
                        })
        if not sources:
            raise ValueError("no independently attributable extraction from the source session")
        for event in queue.list_events(reader_session):
            if event.scope != scope or marker in event.prompt.casefold():
                continue
            response = _response(_native_turn(event), event.turn_id)
            if response is None or marker not in response[1].casefold():
                continue
            for trace in traces:
                if (trace.get("event") != "recalled" or trace.get("session_id") != reader_session
                        or trace.get("turn_id") != event.turn_id or _time(trace["at"]) >= _time(response[0])):
                    continue
                for assertion_id in trace.get("evidence_ids", []):
                    found = sources.get(assertion_id)
                    if found is None:
                        continue
                    source, capture_proof = found
                    if (_time(source.recorded_at) < _time(trace["at"])
                            and _time(capture_proof["source_confirmed_at"]) < _time(trace["at"])
                            and _time(capture_proof["source_extracted_at"]) < _time(trace["at"])):
                        return {"assertion_id": assertion_id, "source_session": source_session,
                                "source_turn": source.turn_id, "reader_session": reader_session,
                                "reader_turn": event.turn_id, "recalled_at": trace["at"],
                                "first_answer_at": response[0], "source_actor": source.actor,
                                "source_transport": source.metadata.get("transport"),
                                "reader_actor": event.actor, "reader_transport": event.metadata.get("transport"),
                                **capture_proof}
    raise ValueError("no native answer used the marker with matching recall evidence before the answer")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-session", required=True)
    parser.add_argument("--reader-session", required=True)
    parser.add_argument("--marker", required=True)
    args = parser.parse_args(argv)
    try:
        proof = verify_sessions(load_config(args.config), args.source_session, args.reader_session, args.marker)
    except (OSError, TypeError, ValueError, KeyError) as error:
        parser.exit(1, f"native acceptance: {error}\n")
    print(json.dumps(proof, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
