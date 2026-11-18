"""Lightweight, opt-in Desktop intake. No retrieval/model work runs in a hook.

Raw-only recovery receipts survive missing Stop events and are replayed by the
next recovery launch. The ordinary prompt path inspects only the bounded native
transcript proof and writes its exact input. Processing uses the MCP supervisor.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from .queue import CaptureQueue, _identity
from .storage import MemoryStore

CONTROL = (
    "Assertion memory captures or stages conversation input automatically; background processing may still be pending. "
    "Do not create capture JSON or run manual capture, history, or status commands each turn. "
    "Answer the conversation normally; query memory only when needed for the actual request. "
    "Questions and assistant proposals are not adopted user decisions."
)


def validate_config(config: dict) -> None:
    if config.get("mode", "legacy") not in ("legacy", "fast"):
        raise ValueError("Desktop mode must be legacy or fast")
    sessions = config.get("native_session_ids")
    if sessions is not None and (not isinstance(sessions, list) or not sessions
            or any(not isinstance(item, str) or not item.strip() for item in sessions)):
        raise ValueError("native_session_ids must be a nonempty physical session allowlist")
    for name in ("logical_session_aliases", "capture_after_offsets", "bootstrap_assistant_context"):
        mapping = config.get(name, {})
        if not isinstance(mapping, dict) or any(not isinstance(key, str) or not key.strip() for key in mapping):
            raise ValueError(f"{name} must be a physical-session-keyed object")
        if mapping and (not sessions or set(mapping) - set(sessions)):
            raise ValueError(f"{name} requires explicitly allowed physical native sessions")
    if any(not isinstance(value, str) or not value.strip()
           for value in config.get("logical_session_aliases", {}).values()):
        raise ValueError("logical session aliases must be nonempty")
    if any(type(value) is not int or value < 0 for value in config.get("capture_after_offsets", {}).values()):
        raise ValueError("capture_after_offsets must be nonnegative byte offsets")
    if "user_actor_id" in config and (not isinstance(config["user_actor_id"], str) or not config["user_actor_id"].strip()):
        raise ValueError("user_actor_id must be nonempty")
    for value in config.get("bootstrap_assistant_context", {}).values():
        if (not isinstance(value, dict) or set(value) != {"turn_id", "message_id"}
                or any(not isinstance(item, str) or not item.strip() for item in value.values())):
            raise ValueError("bootstrap assistant requires explicit turn_id and message_id")
    service = config.get("worker_service")
    if service is not None:
        from .mcp_server import ServiceConfig
        ServiceConfig(**{**service, "db": config["db"]})


def allowed_session(raw: dict, config: dict) -> bool:
    sessions = config.get("native_session_ids")
    return sessions is None or raw.get("session_id") in sessions


def _position(raw, rows, origin):
    if raw.get("_memory_position") is not None:
        return raw["_memory_position"]
    if origin.get("position") is not None:
        return origin["position"]
    starts = [row["_memory_offset"] for row in rows if row.get("type") == "event_msg"
              and row["payload"].get("type") == "task_started"
              and row["payload"].get("turn_id") == raw["turn_id"]]
    return starts[-1] if starts else None


def _capture(queue, raw, config, origin, position, *, staged=False, assistant=None, source_clock=None):
    from .desktop import event_identity
    session, turn, prompt, source = event_identity(raw)
    native = origin.get("origin_kind") == "native-client"
    metadata = {"transport": "codex-native-client" if native else "codex-desktop",
                "cwd": raw["cwd"], "transcript_path": raw["transcript_path"],
                "native_session_id": session, "native_turn_id": turn, "source_position": position}
    if raw.get("_memory_position_unit"):
        metadata["source_position_unit"] = raw["_memory_position_unit"]
        metadata["source_position_origin"] = raw["_memory_position_origin"]
    if raw.get("_memory_unavailable_context_before"):
        metadata["unavailable_context_before"] = raw["_memory_unavailable_context_before"]
    declared = config.get("user_actor_id")
    if declared:
        metadata["declared_user_actor_id"] = declared
    actor = declared or ("codex-client" if native else "user")
    options = {"message_id": raw["_memory_message_id"]} if raw.get("_memory_message_id") else {}
    if assistant is not None:
        prompt, actor = assistant["text"], "assistant"
        source = (f"codex://session/{quote(session, safe='')}/turn/{quote(turn, safe='')}/assistant/"
                  + quote(assistant["message_id"], safe=""))
        metadata["role"] = "assistant"
        options["message_id"] = assistant["message_id"]
    logical = config.get("logical_session_aliases", {}).get(session, session)
    if assistant is None and not raw.get("_memory_message_id"):
        existing = queue.get_event(_identity("event", logical, turn, prompt))
        # A staged steering message has no proven byte position yet. Keep its raw
        # metadata immutable; confirmation adds the exact position in the queue
        # position table, without rewriting a historical intake record.
        metadata["source_position"] = existing.metadata.get("source_position") if existing else (None if staged else position)
        if staged:
            position = None
    # Only controllers provide this argument after physical proof. Raw hook input
    # cannot declare an authenticated source clock by adding metadata of its own.
    from .source_message_clock import normalize, replay, FIELDS
    supplied = normalize(source_clock, source=source)
    existing = queue.get_event(_identity('event', logical, turn, prompt,
        *([actor, options.get('message_id', '')] if options.get('message_id') is not None or actor == 'assistant' else [])))
    if existing is not None:
        replay(existing.metadata, supplied, allow_omitted=source_clock is None, source=source)
        # Exact controller replay may encounter a pre-clock/raw positional
        # recipe. Only absence of these supported optional additions is allowed.
        # The queue's full immutable comparison still checks every base field.
        old_base = {key:value for key,value in existing.metadata.items() if key not in FIELDS}
        expected_base = dict(metadata)
        for key in ('source_position_unit', 'source_position_origin'):
            if key not in old_base:
                expected_base.pop(key, None)
        if old_base != expected_base:
            raise ValueError('raw native event already has different physical provenance')
        metadata = dict(existing.metadata)
    else:
        metadata.update(supplied)
    return queue.capture_event(logical, turn,
        prompt, source=source, actor=actor, scope=config.get("scope", "default"), metadata=metadata,
        await_origin=staged, position=position, **options)


def _receipt_dir(config):
    return Path(config["db"] + ".desktop-recovery")


def _stage_receipt(raw, config):
    """fsync raw recovery input before acknowledging an asynchronous Stop."""
    from .desktop import _json
    root = _receipt_dir(config)
    root.mkdir(parents=True, exist_ok=True)
    payload = _json(raw) + "\n"
    from uuid import uuid4
    key = uuid4().hex
    path = root / (key + ".json")
    if path.exists():
        return
    temporary = root / (key + f".{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def schedule(config_path, config):
    """Only launch a raw recovery child here; it reuses the MCP model supervisor."""
    if not config.get("auto_process", True):
        return
    from .desktop import _environment
    log_path = Path(config["db"] + ".desktop-recovery.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("desktop.py")),
            "recover", "--config", str(config_path.resolve())], stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, cwd=str(Path(__file__).resolve().parents[2]),
            env={**_environment(config), "ASSERTION_MEMORY_DISABLE_HOOKS": "1"},
            start_new_session=True, close_fds=True)


def handle_event(raw, config, config_path=None):
    from .desktop import _native_turn_records, _transcript_prompts, event_identity
    if not allowed_session(raw, config):
        return {}
    event = raw.get("hook_event_name")
    if event not in ("UserPromptSubmit", "Stop"):
        return {}
    # Native proof rejects known internal origins before even staging a receipt.
    if event == "Stop":
        try:
            _, origin = _native_turn_records(raw)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}
        if origin["status"] == "rejected" or origin.get("reason") == "unknown_turn_origin":
            return {}
        _stage_receipt(raw, config)
        if config_path is not None:
            schedule(config_path, config)
        return {}
    event_identity(raw)
    try:
        rows, origin = _native_turn_records(raw)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    if origin["status"] == "rejected":
        return {}
    position = _position(raw, rows, origin)
    if position is None or position < config.get("capture_after_offsets", {}).get(raw["session_id"], 0):
        return {}
    staged = (origin["status"] == "deferred" and origin.get("origin_kind") == "native-client"
              and origin.get("reason") == "native_input_not_yet_proven")
    if origin["status"] != "verified" and not staged:
        return {}
    if not staged:
        matches = [item for item in _transcript_prompts(raw, rows, origin) if item["prompt"] == raw["prompt"]]
        if not matches:
            return {}
        if len(matches) != 1:
            _stage_receipt(raw, config)
            if config_path is not None:
                schedule(config_path, config)
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": CONTROL}}
        raw = matches[0]
        position = raw["_memory_position"]
    _stage_receipt(raw, config)
    Path(config["db"]).parent.mkdir(parents=True, exist_ok=True)
    if not staged:
        with MemoryStore(config["db"]) as store:
            _capture(CaptureQueue(store), raw, config, origin, position,
                     source_clock=raw.get('_memory_source_clock'))
    if config_path is not None:
        schedule(config_path, config)
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": CONTROL}}


def _recover_receipt(raw, config, queue):
    from .desktop import _native_turn_records, _transcript_prompts, _assistant_context
    # Recovery runs with hooks disabled; independently check physical identity/cwd.
    if not allowed_session(raw, config) or raw.get("agent_id") or not isinstance(raw.get("cwd"), str):
        return True, 0
    cwd = Path(raw["cwd"]).resolve()
    if not any(cwd == Path(root).resolve() or Path(root).resolve() in cwd.parents for root in config["cwd_roots"]):
        return True, 0
    rows, origin = _native_turn_records(raw)
    return _recover_records(raw, config, queue, rows, origin)


def _recover_records(raw, config, queue, rows, origin):
    """Shared authenticated-record replay; callers must validate physical source."""
    from .desktop import _transcript_prompts, _assistant_context
    starts = [row for row in rows if row.get("type") == "event_msg"
              and row["payload"].get("type") == "task_started"]
    activation = config.get("capture_after_offsets", {}).get(raw["session_id"], 0)
    bootstrap = config.get("bootstrap_assistant_context", {}).get(raw["session_id"])
    captured, finished = 0, origin["status"] == "rejected"
    for start in starts:
        proof = start["_memory_origin"]
        if proof["status"] != "verified":
            continue
        turn_raw = {**raw, "turn_id": start["payload"]["turn_id"]}
        messages = _assistant_context(turn_raw, rows, proof)
        active = start["_memory_offset"] >= activation
        if active:
            for prompt in _transcript_prompts(turn_raw, rows, proof):
                _capture(queue, prompt, config, proof, prompt["_memory_position"],
                         source_clock=prompt.get('_memory_source_clock'))
                captured += 1
        for message in messages:
            seeded = bootstrap and bootstrap["turn_id"] == turn_raw["turn_id"] and bootstrap["message_id"] == message["message_id"]
            if active or seeded:
                _capture(queue, {**turn_raw, "prompt": message["text"]}, config, proof,
                         message["position"], assistant=message, source_clock=message.get('source_clock'))
                captured += 1
        final_positions = {row["_memory_offset"] for row in rows
                           if row.get("type") == "response_item" and row["payload"].get("role") == "assistant"
                           and row["payload"].get("channel") != "commentary"}
        if turn_raw["turn_id"] == raw["turn_id"] and any(message["position"] in final_positions for message in messages):
            finished = True
    return finished, captured


def recover(config):
    """Raw-only replay, safe before task_complete and before/alongside model work."""
    root = _receipt_dir(config)
    root.mkdir(parents=True, exist_ok=True)
    count = 0
    with (root / "recovery.lock").open("a") as lock:
        # Only detached raw recovery waits here. Waiting ensures a concurrent
        # Stop receipt is not abandoned after an earlier directory snapshot.
        fcntl.flock(lock, fcntl.LOCK_EX)
        with MemoryStore(config["db"]) as store:
            queue = CaptureQueue(store)
            for path in sorted(root.glob("*.json")):
                try:
                    finished, captured = _recover_receipt(json.loads(path.read_text(encoding="utf-8")), config, queue)
                    count += captured
                    if finished:
                        path.unlink(missing_ok=True)
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    # Leave durable receipts for a later lifecycle event/recovery.
                    continue
    return {"status": "recovered", "captured": count}



def seed_assistant_context(raw, config, message_id):
    """Explicit migration bootstrap: verify one physical final, capture no users.

    Run before enabling capture in an existing logical stream. This reads exact
    native transcript bytes; caller-supplied replacement text is never accepted.
    """
    from .desktop import _allowed, _native_turn_records, _assistant_context
    if not _allowed(raw, config) or not allowed_session(raw, config):
        raise ValueError("assistant bootstrap physical session/cwd is not allowed")
    rows, proof = _native_turn_records(raw)
    if proof["status"] != "verified":
        raise ValueError("assistant bootstrap requires verified native origin")
    finals = {row["_memory_offset"] for row in rows if row.get("type") == "response_item"
              and row["payload"].get("role") == "assistant" and row["payload"].get("channel") != "commentary"}
    matches = [message for message in _assistant_context(raw, rows, proof)
               if message["message_id"] == message_id and message["position"] in finals]
    if len(matches) != 1:
        raise ValueError("assistant bootstrap requires one exact visible final message")
    message = matches[0]
    Path(config["db"]).parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore(config["db"]) as store:
        captured = _capture(CaptureQueue(store), {**raw, "prompt": message["text"]}, config, proof,
                            message["position"], assistant=message)
    return {"event_id": captured.id, "source": captured.source, "status": "seeded_assistant_context"}


def launch_worker(config):
    service = config.get("worker_service")
    if not config.get("auto_process", True) or service is None:
        return {"scheduled": False, "reason": "explicit worker_service required"}
    from .mcp_server import ServiceConfig, WorkerLauncher
    return WorkerLauncher(ServiceConfig(**{**service, "db": config["db"]})).start()
