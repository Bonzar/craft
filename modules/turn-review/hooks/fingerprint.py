#!/usr/bin/env python3
"""Отпечаток записей объёма ревью: та же функция и тот же разбор, что у хука.

Подагент ревью запускает этот скрипт по пути объёма и кладёт вывод в поле
вердикта `fingerprint`. Хук сверяет его со своим: не сошлось — после ревью
ход писал ещё, и вердикт не принимается. Нечитаемые строки объёма скрипт
называет в stderr: хук их тоже не считает, отпечаток от них не зависит.
"""

import importlib.util
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
    records, damage = lib.parse_volume_file(Path(argv[1]))
    for problem in damage:
        print(f'нечитаемая строка объёма, {problem}', file=sys.stderr)
    print(lib.fingerprint(records))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
