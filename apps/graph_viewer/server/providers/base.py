"""Read-only normalized SQLite compatibility boundary, with replaceable inputs.

connect() supplies the memory_* record tables consumed by projection/cards/topics.
A provider may adapt a foreign source into a private immutable viewer-owned IR;
those compatibility names do not require using the memory engine or its stores.
Connections must be query-only, and revision metadata belongs to the same pinned
read snapshot. assertion_membership selects IDs before temporal/status filters; its
opaque JSON-safe token fences append reuse, including ownership added after an as-of
cutoff. Providers must fail closed for malformed published membership. No source
constructors or mutation methods cross this boundary.
"""
from typing import Protocol
import sqlite3


SOURCE_MEMBERSHIP_POLICY = 'source-selection-v1'


def membership_checked(manifest, marks):
    preparation=manifest.get('preparation')
    return (isinstance(preparation,dict) and preparation.get('source_membership_policy') == SOURCE_MEMBERSHIP_POLICY
            and isinstance(marks, dict) and marks.get('assertion_membership') is not None)


class GraphSource(Protocol):
    provider_id: str
    def connect(self) -> sqlite3.Connection: ...
    def revision(self) -> str: ...
    def assertion_membership(self, connection: sqlite3.Connection, recorded_cutoff: str) -> tuple[frozenset, object]: ...
    def capabilities(self) -> dict: ...
    def close(self) -> None: ...
    def has_object(self,kind: str,id: str) -> bool: ...
