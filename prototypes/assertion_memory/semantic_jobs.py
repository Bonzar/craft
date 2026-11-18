"""Bind semantic work through program state, never model-written references.

A binding identifies the one source frame, fixed subject pair, or assembly input
of a job. Its output may contain several meanings of that same bound input.
Callers still own semantic schemas and grounding checks: a bound input packet
alone does not prove that each generated sentence follows from its contents.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar
from uuid import uuid4

from .model import Model, ModelError, ModelRequest, _draft_text

BindingT = TypeVar('BindingT')
ValueT = TypeVar('ValueT')


@dataclass(frozen=True)
class BoundSemanticJob(Generic[BindingT]):
    """Keep caller-owned immutable provenance outside the model request.

    The frozen wrapper fixes the association, not the contents of an arbitrary
    binding object. Callers must pin source revisions in their binding itself.
    """
    request: ModelRequest
    binding: BindingT = field(repr=False)


@dataclass(frozen=True)
class BoundSemanticResult(Generic[BindingT, ValueT]):
    binding: BindingT = field(repr=False)
    value: ValueT


@dataclass(frozen=True)
class BoundBatchRequest:
    """Provider transport envelope; only request may enter a model prompt."""
    token: str
    request: ModelRequest


@dataclass(frozen=True)
class BoundBatchResponse:
    """Provider transport correlation; token must not come from model text."""
    token: str
    body: str


class BoundBatchProvider(Protocol):
    def __call__(self, requests: Sequence[BoundBatchRequest]) -> Sequence[BoundBatchResponse]:
        """Preserve program envelope tokens outside every model body.

        This is an explicit provider capability. Packing jobs into a model JSON
        array and asking it to preserve order or echo tokens cannot implement it.
        """


def run_bound_job(model: Model, job: BoundSemanticJob[BindingT],
                  decode: Callable[[str], ValueT]) -> BoundSemanticResult[BindingT, ValueT]:
    """Decode one result while retaining its program-owned source association."""
    value = decode(_draft_text(model(job.request)))
    return BoundSemanticResult(job.binding, value)


def _response_bodies(responses, expected):
    """Validate full coverage before decoding or releasing any result."""
    if not isinstance(responses, Sequence) or isinstance(responses, (str, bytes)):
        raise ModelError('bound batch provider must return response envelopes')
    bodies = {}
    for response in responses:
        if not isinstance(response, BoundBatchResponse):
            raise ModelError('bound batch provider returned a non-envelope response')
        if not isinstance(response.token, str) or response.token not in expected:
            raise ModelError('bound batch provider returned an unknown envelope')
        if response.token in bodies:
            raise ModelError('bound batch provider returned a duplicate envelope')
        bodies[response.token] = _draft_text(response.body)
    if bodies.keys() != expected:
        raise ModelError('bound batch provider omitted a requested envelope')
    return bodies


def run_bound_jobs(provider: BoundBatchProvider, jobs: Sequence[BoundSemanticJob[BindingT]],
                   decode: Callable[[str], ValueT]) -> tuple[BoundSemanticResult[BindingT, ValueT], ...]:
    """Make one explicit batch call; never fall back to one cold start per job.

    Provider envelopes may arrive in any order. Free model arrays play no part
    in identifying jobs or their sources. Output order follows program inputs.
    Provider and decoder failures propagate as errors, never absence verdicts.
    """
    jobs = tuple(jobs)
    if not jobs:
        return ()
    requests = tuple(BoundBatchRequest(uuid4().hex, job.request) for job in jobs)
    bodies = _response_bodies(provider(requests), {row.token for row in requests})
    return tuple(BoundSemanticResult(job.binding, decode(bodies[request.token]))
                 for job, request in zip(jobs, requests))
