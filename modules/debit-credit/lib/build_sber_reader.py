"""Собрать скрипт для вкладки СберБанк Онлайн: только запросы истории за период, без интерфейса."""

import argparse
import json
import os
from pathlib import Path

from build_userscript import page_script, validate_period

BODY = '''
try {
  const snapshot = await collectWithBalances(() => collectSberHistory(OPTIONS), requestSberBalances);
  return { ok: true, snapshot };
} catch (error) {
  return { ok: false, error: error instanceof Error ? error.message : 'Ошибка чтения',
    snapshot: error?.partial || null };
}
'''


def build(output: Path, *, period: dict) -> None:
    validate_period(period)
    reader = page_script('sber_queries.js') + '\n' + page_script('balances.js')
    options = json.dumps(period, ensure_ascii=False)
    text = ('// Read-only Sber history collection.\n(async () => {\n"use strict";\n'
            + reader + '\n' + BODY.replace('OPTIONS', options) + '\n})();\n')
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--from-inclusive', required=True)
    parser.add_argument('--to-exclusive', required=True)
    args = parser.parse_args()
    build(args.output, period={'fromInclusive': args.from_inclusive, 'toExclusive': args.to_exclusive})
