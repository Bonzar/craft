"""Локальный запуск адаптеров. На stdout только сводка, данные — в явный файл."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from adapters.debit_credit import read_csv
from adapters.yandex_bank import read_cards


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Читающие адаптеры учёта расходов')
    commands = result.add_subparsers(dest='adapter', required=True)
    debit = commands.add_parser('debit-credit', help='Чтение русского CSV приложения')
    debit.add_argument('input', type=Path)
    debit.add_argument('--timezone', required=True, help='Например Europe/Moscow')
    debit.add_argument('--currencies', type=Path, help='JSON account: currency, без догадок по имени')
    bank = commands.add_parser('yandex-bank', help='Чтение JSON со снимками открытых карточек')
    bank.add_argument('input', type=Path)
    for command in (debit, bank):
        command.add_argument('--output', type=Path, help='Новый JSON-файл, существующий не перезаписывается')
    return result


def normalize(args: argparse.Namespace) -> dict:
    content = args.input.read_bytes()
    text = content.decode('utf-8-sig')
    if args.adapter == 'debit-credit':
        currencies = None
        if args.currencies:
            currencies = json.loads(args.currencies.read_text(encoding='utf-8'))
        result = read_csv(text, args.timezone, currencies)
    else:
        result = read_cards(json.loads(text))
    return {**result, 'input_sha256': hashlib.sha256(content).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = normalize(args)
        if args.output:
            with args.output.open('x', encoding='utf-8') as target:
                json.dump(result, target, ensure_ascii=False, indent=2)
                target.write('\n')
    except (ValueError, OSError, KeyError) as error:
        # Ошибки JSON/файлов могут включать пользовательские строки: подробности остаются локально.
        print(json.dumps({'error': type(error).__name__,
                          'message': 'Вход или выход не обработан. Проверь формат, пути и часовой пояс.'},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    summary = {key: result[key] for key in ('adapter', 'input_count', 'coverage')}
    summary.update(parsed=len(result['records']), rejected=len(result['rejected']),
                   issues=len(result['issues']), ready=result['ready'])
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
