#!/usr/bin/env python3
"""Безопасный наблюдатель для проверки Cloud-хуков из репозитория.

Его вызывает только native hook в реальном замере. Юнит-тесты подают
синтетические события в отдельный временный state, не в журнал Cloud.
Никакой установки, сборки, чтения Craft или решения о разрешении здесь нет.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


EVENTS = frozenset({'SessionStart', 'PermissionRequest', 'SubagentStart'})
JOURNAL = 'cloud-repo-probe.jsonl'
MARKER = 'JARVIS_CLOUD_HOOK_V1'


def record(raw: dict, *, registration: str | None = None) -> dict:
    """Сохранить только метаданные события, без промпта и содержимого инструментов."""
    if not isinstance(raw, dict) or raw.get('hook_event_name') not in EVENTS:
        raise ValueError('неизвестное событие native hook')
    for key in ('session_id', 'cwd'):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f'в событии не задано поле {key}')
    source = raw.get('source', '')
    if not isinstance(source, str):
        raise ValueError('поле source должно быть строкой')
    state = os.environ.get('JARVIS_STATE_DIR', '')
    if not state.strip():
        raise ValueError('не задан JARVIS_STATE_DIR для журнала пробы Cloud')
    entry = {'event': raw['hook_event_name'], 'source': source,
             'session_id': raw['session_id'], 'cwd': raw['cwd'],
             'pid': os.getpid(), 'at': time.time()}
    if registration is not None:
        entry['registration'] = registration
    root = Path(state)
    root.mkdir(parents=True, exist_ok=True)
    with (root / JOURNAL).open('a', encoding='utf-8') as journal:
        journal.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', choices=('inline-toml',))
    args = parser.parse_args()
    try:
        entry = record(json.load(sys.stdin), registration=args.registration)
    except (OSError, ValueError) as error:
        print(f'ОШИБКА пробы Cloud: {error}', file=sys.stderr)
        return 1
    if entry['event'] == 'SessionStart':
        origin = f" registration={args.registration}" if args.registration else ''
        print(f"{MARKER} source={entry['source']} session={entry['session_id']}{origin}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
