"""Canonical candidate retrieval: reads one projection and never generates an answer."""
from dataclasses import dataclass
import json
from typing import Protocol

from .model import Candidate
from .thought_source_policy import thought_eligibility
from .thought_store import Thought


class CandidateRanker(Protocol):
    def rank_relevance(self, query, candidates, *, limit): ...


@dataclass(frozen=True)
class RetrievalResult:
    thoughts: tuple[Thought, ...]
    candidates: tuple[dict, ...]
    explicit_ids: tuple[str, ...]


class ThoughtRetrieval:
    """One injected ranker, usable with current and pinned thought projections.

    Existing embedding cache semantics belong to the ranker, not publication.
    No generation, answer tracing, publication or scheduling is invoked here.
    """

    def __init__(self, ranker: CandidateRanker):
        self.ranker = ranker

    def search(self, projection, question, explicit_ids, limit, exclude_ids, *, question_texts):
        index = getattr(projection, 'index_entries', None)
        entries = {row['id']: row for row in index()} if index else {
            row.id: {'id': row.id, 'version': row.version, 'text': row.text, 'questions': question_texts(row)}
            for row in projection.list_thoughts()}
        ranked, matches = self.rank_entries(question,
                               {key: row for key, row in entries.items() if key not in exclude_ids}, limit)
        seeds = tuple(explicit_ids)
        selected = dict.fromkeys([row.id for row in ranked] + list(seeds))
        memo = getattr(projection, '_source_policy_memo', {})
        result = []
        for key in selected:
            thought = projection.get(key) if key in entries else None
            if thought is not None and thought_eligibility(projection, thought, memo=memo)['allowed']:
                result.append(thought)
        candidates = tuple({'thought_id': row.id, 'version': entries[row.id]['version'],
                            'score': row.score, 'method': row.method, 'matched_input': matches[row.id]}
                           for row in ranked)
        return RetrievalResult(tuple(result), candidates, seeds)


    def rank_entries(self, query, entries, limit):
        corpus,owners={},{}
        for key,row in entries.items():
            values=[('text',row['text']),*[('question',text) for text in row['questions']]]
            for index,(kind,text) in enumerate(values):
                if not text:continue
                address=json.dumps([key,index],separators=(',',':'))
                corpus[address]=text; owners[address]=(key,kind,text)
        ranked=self.ranker.rank_relevance(query,corpus,limit=len(corpus))
        best={}
        for item in ranked:
            key,kind,text=owners[item.id]
            if key not in best or item.score>best[key][0].score:
                best[key]=(Candidate(key,item.score,item.method),{'kind':kind,'text':text})
        ordered=sorted(best.values(),key=lambda item:(-item[0].score,item[0].id))[:limit]
        return [item for item,_ in ordered],{item.id:match for item,match in ordered}
