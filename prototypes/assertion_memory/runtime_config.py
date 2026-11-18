"""Shared provider configuration for desktop hooks and background work."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from .embeddings import CachedEmbedder, FastEmbedModel, MULTILINGUAL_MODEL
from .model import CandidateSearch, CodexModel, LocalOpenAIEmbedder, LocalOpenAIModel, Model, SubprocessModel
from .storage import MemoryStore


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _required(settings: Mapping[str, str], key: str) -> str:
    value = settings.get(key, "").strip()
    if not value:
        raise ValueError(f"{key} is required for the configured memory provider")
    return value


def build_model(*, environ: Mapping[str, str] | None = None) -> Model | None:
    settings = _env(environ)
    mode = settings.get("ASSERTION_MEMORY_MODEL_MODE", "codex")
    if mode == "extractive":
        return None
    if mode == "codex":
        binary = settings.get("ASSERTION_MEMORY_CODEX_BIN") or shutil.which("codex")
        bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
        if not binary and bundled.is_file():
            binary = str(bundled)
        return CodexModel(binary=binary or "codex", model=settings.get("ASSERTION_MEMORY_MODEL_NAME"))
    if mode == "local":
        return LocalOpenAIModel(
            _required(settings, "ASSERTION_MEMORY_MODEL_ENDPOINT"),
            _required(settings, "ASSERTION_MEMORY_MODEL_NAME"),
        )
    if mode == "command":
        command = json.loads(_required(settings, "ASSERTION_MEMORY_MODEL_COMMAND"))
        if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
            raise ValueError("ASSERTION_MEMORY_MODEL_COMMAND must be a JSON array of arguments")
        return SubprocessModel(command)
    raise ValueError(f"unknown assertion memory model mode: {mode}")


def model_identity(*, environ: Mapping[str, str] | None = None) -> str:
    settings = _env(environ)
    return json.dumps([
        settings.get("ASSERTION_MEMORY_MODEL_MODE", "codex"),
        settings.get("ASSERTION_MEMORY_MODEL_NAME"),
        settings.get("ASSERTION_MEMORY_MODEL_ENDPOINT"),
        settings.get("ASSERTION_MEMORY_MODEL_COMMAND"),
    ])


def build_candidate_search(store: MemoryStore, *, environ: Mapping[str, str] | None = None) -> CandidateSearch:
    settings = _env(environ)
    mode = settings.get("ASSERTION_MEMORY_EMBEDDING_MODE", "lexical")
    if mode == "lexical":
        return CandidateSearch()
    if mode == "fastembed":
        name = settings.get("ASSERTION_MEMORY_EMBEDDING_MODEL", MULTILINGUAL_MODEL)
        cache_dir = settings.get("ASSERTION_MEMORY_EMBEDDING_CACHE") or Path(store.path).resolve().parent / "embedding-models"
        identity = f"fastembed:{name}"
        backend = FastEmbedModel(name, cache_dir)
    elif mode == "local":
        endpoint = _required(settings, "ASSERTION_MEMORY_EMBEDDING_ENDPOINT")
        name = _required(settings, "ASSERTION_MEMORY_EMBEDDING_MODEL")
        identity = f"local:{endpoint}:{name}"
        backend = LocalOpenAIEmbedder(endpoint, name)
    else:
        raise ValueError(f"unknown assertion memory embedding mode: {mode}")
    return CandidateSearch(CachedEmbedder(store, backend, identity), identity=identity)
