"""Source interpretation boundary; the worker owns semantic publication.

The canonical adapter still uses exactly the existing generation and semantic
verification paths. It receives the engine scoped to the attempt, including any
tracing and guarded model replay gateway. It does not change heads, assign
thought identities, renew the claim, or schedule downstream work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .capture import RawEvent
    from .thought_engine import ThoughtEngine
    from .thought_store import Thought

VerifiedSourceDrafts = list[tuple[dict, list[dict]]]


class SourceExtractor(Protocol):
    """Return only drafts accepted by the canonical semantic/provenance checks."""

    def extract(self, event: RawEvent, context: list[RawEvent]) -> VerifiedSourceDrafts: ...

    def resume(self, event: RawEvent, context: list[RawEvent], accepted: list[Thought],
               feedback: list[dict]) -> VerifiedSourceDrafts: ...


class SourceExtractorFactory(Protocol):
    def __call__(self, engine: ThoughtEngine) -> SourceExtractor: ...


class SourceLeaseCheck(Protocol):
    def ensure_active(self) -> None: ...


@dataclass(frozen=True)
class SourceExtractionRequest:
    """Immutable routing identity and a check, not authority to read arbitrary data.

    Only trusted in-process host factories receive this object. The lease check
    is connection-thread-bound; an out-of-process reader must use a separately
    constrained host bridge, not serialize this capability or the engine.
    """
    event_id: str
    scope: str
    capture_session_id: str
    canonical_session_id: str
    lease: SourceLeaseCheck


class ClaimBoundSourceExtractorFactory(Protocol):
    """Host composition hook; do not hand its engine/request to the model."""
    def __call__(self, engine: ThoughtEngine, request: SourceExtractionRequest) -> SourceExtractor: ...


class CanonicalSourceExtractor:
    def __init__(self, engine: ThoughtEngine):
        self.engine = engine

    def extract(self, event: RawEvent, context: list[RawEvent]) -> VerifiedSourceDrafts:
        text, _, origin, records, bases = self.engine._source_packet(event, context)
        return self.engine._prepare_source(records, bases, text, origin)

    def resume(self, event: RawEvent, context: list[RawEvent], accepted: list[Thought],
               feedback: list[dict]) -> VerifiedSourceDrafts:
        return self.engine.resume_source_drafts(event, context, accepted, feedback)
