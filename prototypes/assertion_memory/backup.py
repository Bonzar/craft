"""Consistent SQLite snapshots, including committed WAL content."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def snapshot_database(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".memory-backup-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy)
                # Publish a standalone snapshot without requiring WAL sidecars on restore.
                copy.execute("PRAGMA journal_mode=DELETE")
                if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite integrity check failed")
                tables = {row[0] for row in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"memory_assertions", "memory_edges", "memory_state"} <= tables:
                    raise ValueError("source is not an assertion memory database")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
