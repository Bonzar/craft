#!/usr/bin/env python3
"""Подставной `arc` для тестов: отвечает по сценарию из файла `FAKE_ARC`.

Настоящий arc живёт только в смонтированной Аркадии, в тестах его нет. Сценарий
описывает удалённый trunk и историю папки: какой коммит последним трогал её на
ревизии, когда, и чем отличаются две ревизии. Каждый вызов дописывается в
`calls` сценария — тест видит, что у arc спросили.

Сценарий:

    {"root": "/путь/к/маунту", "trunk": "t2", "fetch_fails": false,
     "log": {"HEAD": ["c1", "2026-10-01T10:00:00+03:00"], "t2": [...]},
     "diff": {"c1..t2": ["путь/файл"]}, "calls": []}
"""

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    scenario_path = Path(os.environ['FAKE_ARC'])
    scenario = json.loads(scenario_path.read_text(encoding='utf-8'))
    scenario.setdefault('calls', []).append(argv)
    scenario_path.write_text(json.dumps(scenario, ensure_ascii=False), encoding='utf-8')

    inside = Path.cwd().resolve().is_relative_to(Path(scenario['root']).resolve())
    if not inside:
        print('Not a mounted arc repository', file=sys.stderr)
        return 1
    command = argv[0]
    if command == 'info':
        print('repository: arcadia')
        return 0
    if command == 'fetch':
        if scenario.get('fetch_fails'):
            print('error: fetch: сеть недоступна', file=sys.stderr)
            return 1
        return 0
    if command == 'rev-parse':
        print(scenario['trunk'] if argv[1] == 'arcadia/trunk' else argv[1])
        return 0
    if command == 'log':
        rev = argv[argv.index('--') - 1]
        rev = scenario['trunk'] if rev == 'arcadia/trunk' else rev
        found = scenario.get('log', {}).get(rev)
        if found:
            print('\t'.join(found))
        return 0
    if command == 'diff':
        old, new = argv[2], argv[3]
        print('\n'.join(scenario.get('diff', {}).get(f'{old}..{new}', [])))
        return 0
    print(f'неизвестная команда {command}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
