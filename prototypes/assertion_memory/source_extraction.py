"""Source interpretation boundary; the worker owns semantic publication.

The canonical adapter still uses exactly the existing generation and semantic
verification paths. It receives the engine scoped to the attempt, including any
tracing and guarded model replay gateway. It does not change heads, assign
thought identities, renew the claim, or schedule downstream work.
"""
from __future__ import annotations

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


class CanonicalSourceExtractor:
    def __init__(self, engine: ThoughtEngine):
        self.engine = engine

    def extract(self, event: RawEvent, context: list[RawEvent]) -> VerifiedSourceDrafts:
        text, _, origin, records, bases = self.engine._source_packet(event, context)
        return self.engine._prepare_source(records, bases, text, origin)

    def resume(self, event: RawEvent, context: list[RawEvent], accepted: list[Thought],
               feedback: list[dict]) -> VerifiedSourceDrafts:
        return self.engine.resume_source_drafts(event, context, accepted, feedback)
