"""Собрать скрипт для вкладки Т-Банка: только запросы ленты своих счетов, без интерфейса."""

import argparse
import json
import os
import re
from pathlib import Path

from build_userscript import validate_period

BODY = '''
try {
  const snapshot = await collectTbankHistory(OPTIONS);
  return { ok: true, snapshot };
} catch (error) {
  return { ok: false, error: error instanceof Error ? error.message : 'Ошибка чтения',
    snapshot: error?.partial || null };
}
'''


def validate_accounts(accounts: list) -> None:
    if (not isinstance(accounts, list) or not accounts
            or any(not isinstance(value, str) or not re.fullmatch(r'\d+', value) for value in accounts)):
        raise ValueError('Нужен непустой список номеров своих счетов')


def build(output: Path, *, period: dict, accounts: list) -> None:
    validate_period(period)
    validate_accounts(accounts)
    options = json.dumps({**period, 'accounts': accounts}, ensure_ascii=False)
    reader = Path(__file__).with_name('tbank_queries.js').read_text(encoding='utf-8')
    reader = reader.split("\nif (typeof module !== 'undefined')")[0]
    text = ('// Read-only T-Bank timeline collection.\n(async () => {\n"use strict";\n'
            + reader + '\n' + BODY.replace('OPTIONS', options) + '\n})();\n')
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--from-inclusive', required=True)
    parser.add_argument('--to-exclusive', required=True)
    parser.add_argument('--account', action='append', required=True, help='Номер своего счёта')
    args = parser.parse_args()
    build(args.output, period={'fromInclusive': args.from_inclusive, 'toExclusive': args.to_exclusive},
          accounts=args.account)
