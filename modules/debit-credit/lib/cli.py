"""Локальный запуск адаптеров. На stdout только сводка, данные — в явный файл."""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from adapters.debit_credit import read_csv
from adapters.debit_credit_backup import read_backup
from adapters.yandex_bank import read_cards
from adapters.matching import compare_records
from adapters.yandex_bank_api import read_history
from adapters.import_verification import verify_additions
from adapters.restore_verification import verify_restoration
from adapters.backup_edit import prepare_backup
from adapters.backup_registry import register_backup


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Адаптеры учёта и подготовка отдельных резервных копий')
    commands = result.add_subparsers(dest='adapter', required=True)
    debit = commands.add_parser('debit-credit', help='Чтение русского CSV приложения')
    debit.add_argument('input', type=Path)
    debit.add_argument('--timezone', required=True, help='Например Europe/Moscow')
    debit.add_argument('--currencies', type=Path, help='JSON account: currency, без догадок по имени')
    bank = commands.add_parser('yandex-bank', help='Чтение JSON со снимками открытых карточек')
    bank.add_argument('input', type=Path)
    api = commands.add_parser('yandex-bank-api', help='Чтение JSON сборщика запросов банка')
    api.add_argument('input', type=Path)
    backup = commands.add_parser('debit-credit-backup', help='Чтение отдельной копии backup + WAL')
    backup.add_argument('input', type=Path)
    backup.add_argument('--timezone', required=True)
    backup.add_argument('--from-date')
    backup.add_argument('--to-date')
    compare = commands.add_parser('compare', help='Связи по меткам и кандидаты старых записей')
    compare.add_argument('input', type=Path, help='JSON результата адаптера источника')
    compare.add_argument('ledger', type=Path, help='JSON результата адаптера приложения')
    compare.add_argument('--accounts', type=Path, required=True)
    compare.add_argument('--window-seconds', type=int, required=True)
    verify = commands.add_parser('verify-import', help='Проверка добавлений по полным backup до и после')
    verify.add_argument('input', type=Path, help='Резервная копия до импорта')
    verify.add_argument('after', type=Path, help='Резервная копия после импорта')
    verify.add_argument('--expected', type=Path, required=True)
    verify.add_argument('--timezone', required=True)
    restore = commands.add_parser('verify-restore', help='Полная сверка операций и счетов после восстановления')
    restore.add_argument('input', type=Path, help='Подготовленная резервная копия')
    restore.add_argument('after', type=Path, help='Копия, созданная приложением после восстановления')
    restore.add_argument('--timezone', required=True)
    prepare = commands.add_parser('prepare-backup', help='Явные правки в отдельной копии без записи в приложение')
    prepare.add_argument('input', type=Path)
    prepare.add_argument('--plan', type=Path, required=True)
    prepare.add_argument('--target', type=Path, required=True)
    prepare.add_argument('--timezone', required=True)
    register = commands.add_parser('register-backup', help='Добавление копии в BackupList закрытого Mac-приложения')
    register.add_argument('input', type=Path)
    register.add_argument('--report', type=Path, required=True)
    register.add_argument('--preferences', type=Path, required=True)
    register.add_argument('--source-filename', required=True)
    register.add_argument('--timestamp', required=True)
    register.add_argument('--registry-archive', type=Path, required=True)
    for command in (debit, bank, api, backup, compare, verify, restore, prepare, register):
        command.add_argument('--output', type=Path, help='Новый JSON-файл, существующий не перезаписывается')
    return result


def normalize(args: argparse.Namespace) -> dict:
    if args.adapter == 'register-backup':
        if args.output and (args.output.exists() or args.output.is_symlink()):
            raise FileExistsError('Отчёт должен быть новым отдельным файлом')
        report = json.loads(args.report.read_text(encoding='utf-8-sig'))
        return register_backup(args.input, report, args.preferences, args.source_filename,
                               datetime.fromisoformat(args.timestamp), args.registry_archive)
    if args.adapter == 'prepare-backup':
        if args.output and (args.output.exists() or args.output.resolve() == args.target.resolve()):
            raise FileExistsError('Отчёт должен быть новым отдельным файлом')
        plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
        return prepare_backup(args.input, args.target, plan, args.timezone)
    if args.adapter == 'verify-restore':
        inputs = backup_hashes(args.input, args.after)
        expected = read_backup(args.input, args.timezone)
        actual = read_backup(args.after, args.timezone)
        if inputs != backup_hashes(args.input, args.after):
            raise ValueError('Резервные копии изменились во время сверки')
        result = verify_restoration(expected, actual)
        return {**result, 'inputs_sha256': inputs}
    if args.adapter == 'verify-import':
        expected_bytes = args.expected.read_bytes()
        expected = json.loads(expected_bytes.decode('utf-8-sig'))
        before = read_backup(args.input, args.timezone)
        after = read_backup(args.after, args.timezone)
        result = verify_additions(before, after, expected)
        return {**result, 'expected_sha256': hashlib.sha256(expected_bytes).hexdigest()}
    if args.adapter == 'compare':
        return compare_snapshots(args)
    if args.adapter == 'debit-credit-backup':
        return read_backup(args.input, args.timezone, args.from_date, args.to_date)
    content = args.input.read_bytes()
    text = content.decode('utf-8-sig')
    if args.adapter == 'debit-credit':
        currencies = None
        if args.currencies:
            currencies = json.loads(args.currencies.read_text(encoding='utf-8'))
        result = read_csv(text, args.timezone, currencies)
    elif args.adapter == 'yandex-bank-api':
        result = read_history(json.loads(text))
    else:
        result = read_cards(json.loads(text))
    return {**result, 'input_sha256': hashlib.sha256(content).hexdigest()}


def backup_hashes(expected: Path, actual: Path) -> dict:
    result = {}
    for label, path in (('expected', expected), ('actual', actual)):
        result[label] = hashlib.sha256(path.read_bytes()).hexdigest()
        wal = Path(str(path) + '-wal')
        result[label + '_wal'] = hashlib.sha256(wal.read_bytes()).hexdigest() if wal.exists() else None
    return result


def read_snapshot(path: Path) -> tuple[dict, str]:
    content = path.read_bytes()
    value = json.loads(content.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Ожидается результат адаптера')
    if (not isinstance(value.get('records'), list) or not isinstance(value.get('rejected'), list)
            or type(value.get('input_count')) is not int
            or value['input_count'] != len(value['records']) + len(value['rejected'])):
        raise ValueError('Не сошёлся счёт входных записей адаптера')
    return value, hashlib.sha256(content).hexdigest()


def compare_snapshots(args: argparse.Namespace) -> dict:
    source, source_hash = read_snapshot(args.input)
    ledger, ledger_hash = read_snapshot(args.ledger)
    map_bytes = args.accounts.read_bytes()
    mapping = json.loads(map_bytes.decode('utf-8-sig'))
    result = compare_records(source['records'], ledger['records'], mapping,
                             window_seconds=args.window_seconds)
    return {**result, 'input_count': source['input_count'], 'rejected': source['rejected'],
            'source_input': {key: value for key, value in source.items() if key != 'records'},
            'ledger_input': {key: value for key, value in ledger.items() if key != 'records'},
            'inputs_sha256': {'source': source_hash, 'ledger': ledger_hash,
                              'accounts': hashlib.sha256(map_bytes).hexdigest()}}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = normalize(args)
        if args.output:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
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
