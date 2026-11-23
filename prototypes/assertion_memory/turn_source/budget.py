"""Trusted provider token accounting. No implicit characters-per-token estimate."""
from dataclasses import dataclass
from typing import Callable
import json
from contextlib import contextmanager
from contextvars import ContextVar

from .reader import SourceReadDenied
from .transport import MAX_FRAME

# Source text is nested in packet JSON, prompt text and transport JSON.
MAX_SOURCE_FRAME_BYTES = MAX_FRAME // 8

def frame_fits(text):
    return len(json.dumps(text,ensure_ascii=False).encode()) < MAX_SOURCE_FRAME_BYTES


class SourceBudgetExceeded(SourceReadDenied):
    """An incomplete attempt must be retried, never marked processed."""


@dataclass(frozen=True)
class TokenBudget:
    window: int
    count: Callable[[str], int]
    tokenizer: str
    instructions: int = 4096
    output: int = 4096
    references: int = 8192

    def __post_init__(self):
        if (not self.tokenizer or not callable(self.count)
                or any(type(x) is not int or x < 0 for x in
                       (self.window,self.instructions,self.output,self.references))
                or self.capacity <= 0):
            raise ValueError('Provider window and named tokenizer must leave a usable source budget')

    @property
    def capacity(self):
        return self.window-self.instructions-self.output-self.references

    def tokens(self, value):
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        size=self.count(text)
        if type(size) is not int or size < 0:
            raise SourceBudgetExceeded('Invalid provider token count')
        return size

    def require(self, value, *, reserve=0):
        size=self.tokens(value)
        if size+reserve > self.capacity:
            raise SourceBudgetExceeded('source_token_budget_exhausted: '+self.tokenizer)
        return size

    def chunks(self, text, *, overhead=0, measure=None):
        """Every original character, with exact Python/DB offsets; no decoding slices."""
        available=self.capacity-overhead
        if available <= 0:
            raise SourceBudgetExceeded('No room for source after instructions and references')
        measure=measure or (lambda body:self.tokens({'text':body}))
        start=0
        while start < len(text):
            lo,hi=start+1,len(text)
            best=start
            while lo <= hi:
                end=(lo+hi)//2
                if measure(text[start:end]) <= available and frame_fits(text[start:end]):
                    best=end;lo=end+1
                else:hi=end-1
            if best==start:
                raise SourceBudgetExceeded('A source character cannot fit the provider budget')
            yield start,best,text[start:best]
            start=best


def fixture_budget():
    # Only the disabled-live protocol fixture uses this. UTF-8 bytes are a
    # conservative byte-token upper bound, NOT a real provider tokenizer.
    return TokenBudget(262144,lambda text:len(text.encode('utf-8')),
                       'offline-fixture:utf8-byte-upper-bound')

_ACTIVE = ContextVar('source_token_budget', default=None)

@contextmanager
def using_budget(budget):
    token=_ACTIVE.set(budget)
    try:yield
    finally:_ACTIVE.reset(token)

def check_request(request):
    budget=_ACTIVE.get()
    if budget is not None:
        from ..model import format_prompt
        budget.require(format_prompt(request),reserve=budget.tokens(request.response_schema))
