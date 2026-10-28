"""Собрать читающий скрипт Яндекс Банка под период: только запросы, без интерфейса и cookies."""

import os
import argparse
import json
from datetime import datetime
from pathlib import Path

def validate_period(period: dict) -> None:
    if set(period) != {'fromInclusive', 'toExclusive'}:
        raise ValueError('Нужны начало и конец периода')
    dates = [datetime.fromisoformat(period[key]) for key in ('fromInclusive', 'toExclusive')]
    if any(value.utcoffset() is None for value in dates) or dates[0] >= dates[1]:
        raise ValueError('Нужен возрастающий период с явным часовым поясом')


def headless_body(period: dict, recheck_ids: list[str]) -> str:
    validate_period(period)
    options = json.dumps({**period, 'recheckIds': recheck_ids}, ensure_ascii=False)
    return '''
if (window.location.origin !== 'https://bank.yandex.ru'
    || !['/my', '/my/transactions-history'].includes(window.location.pathname)) {
  throw new Error('Запуск нужен на главной странице или в истории банка');
}
try {
  const snapshot = await collectHistoryWithBalance({ ...PERIOD, concurrency: 4,
    readBalance: requestCardBalance, balanceSourceUrl: 'https://bank.yandex.ru/my' });
  return { ok: true, snapshot };
} catch (error) {
  return { ok: false, error: error instanceof Error ? error.message : 'Ошибка чтения',
    snapshot: error?.partial || null };
}
'''.replace('PERIOD', options)


def build(output: Path, *, period: dict, recheck_ids: list[str] | None = None) -> None:
    ids = [] if recheck_ids is None else recheck_ids
    if (not isinstance(ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in ids)):
        raise ValueError('Для повторного чтения нужен массив непустых ID')
    body = headless_body(period, ids)
    reader = Path(__file__).with_name('yandex_bank_queries.js').read_text(encoding='utf-8')
    reader = reader.split("\nif (typeof module !== 'undefined')")[0]
    capture = Path(__file__).with_name('yandex_bank_capture.js').read_text(encoding='utf-8')
    capture = capture.split("\nif (typeof module !== 'undefined')")[0]
    text = '// Read-only Yandex Bank history collection.\n' + '\n(async () => {\n"use strict";\n' + reader + '\n' + capture + '\n' + body + '\n})();\n'
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
