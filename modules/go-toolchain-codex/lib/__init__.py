"""Контракт адаптера сборщиков Codex: исполняемый файл и среда сборки."""

import json
from pathlib import Path

from jarvis import registry


def prepare_build(runtime) -> tuple[str, dict]:
    dependency = registry.find('go-toolchain', Path(__file__).resolve().parents[1])
    if dependency is None:
        raise RuntimeError('для подготовки сборки нужен модуль go-toolchain')
    tool, environment = dependency.load().prepare()
    runtime.storage.append_line('go-toolchain.jsonl', json.dumps({
        'module': runtime.manifest.slug, 'tool': tool,
        'gocache': environment['GOCACHE'], 'gopath': environment['GOPATH'],
    }, ensure_ascii=False))
    return tool, environment

