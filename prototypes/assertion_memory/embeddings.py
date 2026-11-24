"""Model-specific, durable embedding reuse for both search and linking."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import struct
from collections.abc import Sequence
from pathlib import Path

from .model import Embedder, ModelError, _embedding_vectors
from .storage import MemoryStore


_BATCH_SIZE = 64
_LOOKUP_SIZE = 400
MULTILINGUAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


_persist_embeddings = ContextVar('persist_embeddings', default=True)


@contextmanager
def read_only_embeddings():
    token = _persist_embeddings.set(False)
    try:
        yield
    finally:
        _persist_embeddings.reset(token)


class FastEmbedModel:
    def __init__(self, model_name: str, cache_dir: str | Path) -> None:
        self.model_name = model_name
        self.cache_dir = str(cache_dir)
        self._model = None

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(model_name=self.model_name, cache_dir=self.cache_dir, threads=2)
            return [vector.tolist() for vector in self._model.embed(list(texts), batch_size=_BATCH_SIZE)]
        except ImportError as error:
            module = error.name or "native embedding library"
            raise ModelError(f"FastEmbed dependency could not load: {module}") from error
        except (OSError, RuntimeError, ValueError) as error:
            raise ModelError(f"FastEmbed backend unavailable ({type(error).__name__})") from error


class CachedEmbedder:
    """Cache float32 vectors by exact text and embedding backend identity."""

    def __init__(self, store: MemoryStore, backend: Embedder, identity: str) -> None:
        self.store = store
        self.backend = backend
        self.identity = identity
        with store.transaction(write=True) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_embedding_cache (
                    backend TEXT NOT NULL, text_digest TEXT NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY (backend, text_digest)
                )
            """)

    def __call__(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
        text_by_key = dict(zip(keys, texts))
        distinct = list(text_by_key)
        stored = self._load(distinct)
        missing = [key for key in distinct if key not in stored]
        for offset in range(0, len(missing), _BATCH_SIZE):
            batch = missing[offset:offset + _BATCH_SIZE]
            vectors = _embedding_vectors(self.backend([text_by_key[key] for key in batch]), len(batch))
            packed = {key: struct.pack(f"<{len(vector)}f", *vector) for key, vector in zip(batch, vectors)}
            if _persist_embeddings.get():
                with self.store.transaction(write=True) as connection:
                    connection.executemany(
                        "INSERT OR IGNORE INTO memory_embedding_cache VALUES (?, ?, ?)",
                        [(self.identity, key, blob) for key, blob in packed.items()],
                    )
                stored.update(self._load(batch))
            else:
                stored.update(packed)
        return _embedding_vectors(
            [struct.unpack(f"<{len(stored[key]) // 4}f", stored[key]) for key in keys], len(keys),
        )

    def covers(self, texts: Sequence[str]) -> bool:
        keys = list(dict.fromkeys(hashlib.sha256(text.encode()).hexdigest() for text in texts))
        return len(self._load(keys)) == len(keys)

    def _load(self, keys: list[str]) -> dict[str, bytes]:
        result = {}
        with self.store.transaction() as connection:
            for offset in range(0, len(keys), _LOOKUP_SIZE):
                batch = keys[offset:offset + _LOOKUP_SIZE]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(f"""
                    SELECT text_digest, vector FROM memory_embedding_cache
                    WHERE backend = ? AND text_digest IN ({placeholders})
                """, [self.identity, *batch])
                result.update({row[0]: row[1] for row in rows})
        return result
