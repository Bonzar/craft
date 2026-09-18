#!/usr/bin/env python3
"""База стартового контекста Codex: собирает у поставщиков и печатает всё разом.

Поставщики те же, что у базы Claude, и ищутся так же — адаптеры с
`for = ["start-context-*"]`. Разница только в харнесе: у Codex потолок ответа
снимается ключом `additionalContextLimit = 0` в строке хука, который пишет
установщик, поэтому копия одна и цепочки нет.

Общего модуля `start-context` у двух баз нет намеренно: общее у них — только
поиск своих адаптеров, и он лежит в ядре. Всё остальное у каждой базы своё,
потому что своё у харнесов.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import registry  # noqa: E402
from jarvis import wrappers  # noqa: E402

STATE_FILE = 'start-context.json'
JOURNAL_FILE = 'start-context.jsonl'
PROVIDE = 'provide'
DATA_GLOB = '*.md'


def provider_text(adapter, event, storage) -> str:
    """Текст одного поставщика: у кодового — функция, у данных — файлы как есть."""
    library = registry.library_of(adapter)
    if library is not None:
        provide = getattr(library.load(), PROVIDE, None)
        if provide is None:
            raise AttributeError(f'в lib поставщика «{adapter.slug}» нет функции {PROVIDE}')
        return provide(event, storage) or ''
    data = registry.data_of(adapter)
    if data is None:
        return ''
    return '\n\n'.join(path.read_text(encoding='utf-8') for path in sorted(data.glob(DATA_GLOB)))


def gather(runtime, event) -> tuple[str, list[dict]]:
    """Склейка по slug поставщика, с заголовком-разделителем."""
    pieces = []
    accounted = []
    for adapter in registry.adapters(runtime.module_dir, runtime.manifest.slug):
        try:
            text = (provider_text(adapter, event, runtime.storage) or '').strip()
            error = None
        except Exception as failure:  # noqa: BLE001 — причина уходит в учёт
            text, error = '', f'{type(failure).__name__}: {failure}'
        accounted.append({'slug': adapter.slug, 'chars': len(text), 'error': error})
        if text:
            pieces.append(f'## {adapter.slug}\n\n{text}')
    return '\n\n'.join(pieces), accounted


class Module(jarvis.Module):
    """Одна копия: собрала и напечатала всё."""

    def handle(self, event, runtime):
        text, accounted = gather(runtime, event)
        runtime.storage.write_json(STATE_FILE, {
            'event': event.event,
            'at': time.time(),
            'total_chars': len(text),
            'providers': accounted,
        })
        runtime.storage.append_line(JOURNAL_FILE, json.dumps(
            {'at': time.time(), 'answer': 'context' if text else 'silence', 'chars': len(text)},
            ensure_ascii=False,
        ))
        return jarvis.Context(text) if text else jarvis.Silence()


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
