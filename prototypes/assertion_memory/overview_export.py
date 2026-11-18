"""Replay initial writer projections without invoking a model or rebuilding views."""
from __future__ import annotations

import json

from .model import ModelError
from .overview import _model_record
from .overview_projection import upstream_parts
from .overview_review import (
    SYNTHESIS_BUDGET, _SYNTHESIZE, RetrievalLedger, _composition_groups,
    _digest, _plan_writer_request, _model_contribution,
    _node_links, _query_frame, _round_inputs, corpus_revision,
)


def initial_writer_requests(spec,evidence,links,missing,cutoffs,*,upstream_contributions=(),
                            query_context=None,revision=None):
    records={row['id']:row for row in evidence}
    revision=revision or corpus_revision(spec,records,links,evidence)
    ledger=RetrievalLedger(None,records,set(records))
    ledger.cache_context=revision
    frame=query_context or _query_frame(spec,cutoffs,revision)
    parts=upstream_parts(upstream_contributions,evidence,links,spec.scopes,cutoffs)
    represented={row['id'] for part in parts for row in part['records']}
    rows,batches,direct=_round_inputs(spec,evidence,links,missing,parts,represented,frame,ledger,{},[],cutoffs)
    if direct:
        return [_plan_writer_request(spec,rows,links,frame,ledger,'global')[0]]
    if batches:
        return [_plan_writer_request(spec,[_model_record(row) for row in batch],edges,
                    frame,ledger,'local',packet_id=_digest([batch,edges]))[0] for batch,edges in batches]
    return _semantic_requests(spec,parts,links,frame,ledger)


def _semantic_requests(spec,parts,links,frame,ledger):
    def request(children,phase='composition'):
        key=None if phase=='global' else 'compose-'+_digest([part['key'] for part in children])
        rows=[_model_contribution(part,ledger) for part in children]
        edges=links if phase=='global' else _node_links(children,links)
        writer,_=_plan_writer_request(spec,rows,edges,frame,ledger,phase,packet_id=key,task=_SYNTHESIZE)
        return writer
    try:
        global_request=request(parts,'global')
        fits=True
    except ModelError:
        fits=False
    if fits and len(json.dumps([_model_contribution(part,ledger) for part in parts],
                               ensure_ascii=False))<=SYNTHESIS_BUDGET:
        return [global_request]
    groups=_composition_groups(parts,links,request,lambda part:_model_contribution(part,ledger))
    if len(groups)>=len(parts):
        raise ModelError('verified semantic conditions cannot be composed within budget; no facts discarded')
    return [request(children) for children in groups]
