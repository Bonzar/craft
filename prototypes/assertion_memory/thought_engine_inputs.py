"""Lossless bounded source slicing; addresses are assigned only by code."""
from __future__ import annotations
import json

from .model import ModelError

RAW_CHUNK_CHARACTERS = 20000
MIN_CHUNK_CHARACTERS = 1000


def quote_span(text, quote):
    """Locate logical quotations also inside serialized message/annotation strings.

    Addresses always cover the ORIGINAL bytes-as-characters, including JSON
    escapes. No fuzzy meaning match, generated ID or model-provided offset.
    """
    forms = [quote]
    for ascii_only in (False,True):
        encoded = quote
        for _ in range(2):
            encoded = json.dumps(encoded,ensure_ascii=ascii_only)[1:-1]
            if encoded not in forms:forms.append(encoded)
    for form in forms:
        start = text.find(form)
        if start >= 0:return start,start+len(form)
    return None


def paragraph_chunks(text, limit=RAW_CHUNK_CHARACTERS):
    if type(limit) is not int or limit < MIN_CHUNK_CHARACTERS:
        raise ModelError('authorized context leaves no useful bounded source window')
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = text.rfind('\n\n', start + limit // 2, end)
            if boundary >= 0:
                end = boundary + 2
            else:
                boundary = text.rfind('\n', start + limit // 2, end)
                if boundary >= 0:
                    end = boundary + 1
        yield {'start': start, 'end': end}, text[start:end]
        start = end
