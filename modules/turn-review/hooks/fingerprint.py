#!/usr/bin/env python3
"""Отпечаток записей объёма ревью: та же функция, что у хука.

Подагент ревью запускает этот скрипт по пути объёма и кладёт вывод в поле
вердикта `fingerprint`. Хук сверяет его со своим: не сошлось — после ревью
ход писал ещё, и вердикт не принимается.
"""

import importlib.util
import json
import sys
from pathlib import Path

LIB_FILE = Path(__file__).resolve().parents[1] / 'lib' / '__init__.py'


def load_lib():
    spec = importlib.util.spec_from_file_location('turn_review_lib', LIB_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('использование: fingerprint.py ‹путь к объёму›', file=sys.stderr)
        return 2
    lib = load_lib()
    records = []
    for line in Path(argv[1]).read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    print(lib.fingerprint(records))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
