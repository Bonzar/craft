"""Offline MCP tools/list compatibility gate against the original Craft server.

From repository root:
    python -m apps.memory_sites.mcp_parity --emit-original > /tmp/original-tools.json
    python -m apps.memory_sites.mcp_parity /path/to/candidate-tools-list.json

The candidate file contains a tools/list reply from an authorized MCP client,
not a token, raw memory or a dump of a prior session. No URL, live database,
model provider or credentials are used by this CLI. Passing this gate checks
only method inventory and input schemas, not behavioral parity.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path


def _canonical(value):
    # JSON Schema's required/enum arrays are order-insensitive. Ignore only
    # presentation metadata; defaults, descriptions outside the input shape,
    # types, validation, and additional-properties rules are not inferred.
    if isinstance(value, dict):
        return {
            key: sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
            if key in {"required", "enum"} and isinstance(items, list)
            else _canonical(items)
            for key, items in sorted(value.items())
            if key != "title" or not isinstance(items, str)
        }
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _read_list(value):
    if isinstance(value, Mapping):
        value = value.get("result", value)
        if isinstance(value, Mapping):
            if value.get("nextCursor"):
                raise ValueError("tools/list is paginated; combine all pages first")
            value = value.get("tools")
    if not isinstance(value, list):
        raise ValueError("expected a tools/list JSON result")
    result = {}
    for entry in value:
        if (not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str)
                or not isinstance(entry.get("inputSchema"), dict) or entry["name"] in result):
            raise ValueError("invalid or duplicate tool definition")
        result[entry["name"]] = _canonical(entry["inputSchema"])
    return result


def compare(original, candidate):
    expected = _read_list(original)
    actual = _read_list(candidate)
    common = expected.keys() & actual.keys()
    return {
        "expected_tools": len(expected),
        "candidate_tools": len(actual),
        "missing": sorted(expected.keys() - actual.keys()),
        "unexpected": sorted(actual.keys() - expected.keys()),
        "different_input_schema": sorted(name for name in common if expected[name] != actual[name]),
    }


def original_tools():
    from prototypes.assertion_memory.mcp_server import ServiceConfig, create_server
    from prototypes.assertion_memory.storage import MemoryStore

    with tempfile.TemporaryDirectory(prefix="craft-memory-contract-") as directory:
        database = str(Path(directory) / "scratch.sqlite3")
        with MemoryStore(database) as store:
            server = create_server(ServiceConfig(db=database, provider="none"), background=False, store=store)
            tools = asyncio.run(server.list_tools())
    return {"tools": [{"name": t.name, "inputSchema": t.inputSchema} for t in tools]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", nargs="?", type=Path)
    parser.add_argument("--emit-original", action="store_true")
    args = parser.parse_args(argv)
    if args.emit_original == (args.candidate is not None):
        parser.error("supply exactly one of --emit-original or a tools/list JSON file")
    original = original_tools()
    if args.emit_original:
        print(json.dumps(original, ensure_ascii=False, indent=2))
        return 0
    try:
        with args.candidate.open("rb") as f:
            blob = f.read(2_000_001)
        if len(blob) > 2_000_000:
            raise ValueError("tools/list file exceeds 2 MB")
        result = compare(original, json.loads(blob))
    except (OSError, ValueError) as error:
        print(f"Cannot compare candidate tool metadata: {type(error).__name__}.", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if any(result[key] for key in ("missing", "unexpected", "different_input_schema")) else 0


if __name__ == "__main__":
    sys.exit(main())
