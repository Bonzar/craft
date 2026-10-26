"""Локальный запуск адаптеров. На stdout только сводка, данные — в явный файл."""

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from adapters.debit_credit import read_csv
from adapters.debit_credit_backup import read_backup
from adapters.yandex_bank import read_cards
from adapters.matching import compare_records
from adapters.yandex_bank_api import read_history
from adapters.import_verification import verify_additions
from adapters.restore_verification import verify_restoration
from adapters.backup_edit import prepare_backup, backup_fingerprint
from adapters.backup_registry import register_backup
from adapters.review import build_review
from adapters.source_resolutions import apply_resolutions
from adapters.yandex_accounting import expand_accounting
from adapters.classification import add_classification_evidence
from adapters.movement_control import compare_movements
from adapters.transfer_plan import build_transfer_plan
from adapters.transfer_accounting import expand_transfers
from adapters.completion import plan_confirmations


def transfer_command(commands):
    command = commands.add_parser('plan-transfers', help='План новых переводов от полной копии')
    command.add_argument('input', type=Path)
    command.add_argument('--backup', type=Path, required=True)
    command.add_argument('--accounts', type=Path, required=True)
    command.add_argument('--from-date', required=True)
    command.add_argument('--to-date', required=True)
    command.add_argument('--timezone', required=True)
    command.add_argument('--pair-window-seconds', type=int, required=True)
    command.add_argument('--ledger-window-seconds', type=int, required=True)
    return command


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Адаптеры учёта и подготовка отдельных резервных копий')
    commands = result.add_subparsers(dest='adapter', required=True)
    transfers = transfer_command(commands)
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
    review = commands.add_parser('review', help='Полный разбор сопоставления с контекстом и группами')
    review.add_argument('input', type=Path, help='JSON результата compare')
    confirmations = commands.add_parser('plan-confirmations', help='Подтверждения прежних незавершённых записей')
    confirmations.add_argument('input', type=Path, help='JSON результата compare')
    classification = commands.add_parser('classification-evidence', help='Варианты категорий по истории')
    classification.add_argument('input', type=Path, help='JSON результата review')
    movement = commands.add_parser('movement-control', help='Движение по счетам без подтверждения остатков')
    movement.add_argument('input', type=Path)
    movement.add_argument('--ledger', type=Path, required=True)
    movement.add_argument('--accounts', type=Path, required=True)
    movement.add_argument('--from-date', required=True)
    movement.add_argument('--to-date', required=True)
    movement.add_argument('--timezone', required=True)
    resolve = commands.add_parser('resolve-source', help='Подтверждения для точных операций источника')
    resolve.add_argument('input', type=Path)
    resolve.add_argument('--resolutions', type=Path, required=True)
    accounting = commands.add_parser('yandex-accounting', help='Ожидаемые округления и кешбэк')
    accounting.add_argument('input', type=Path)
    accounting.add_argument('--parent-window-seconds', type=int, required=True)
    transfer_accounting = commands.add_parser('transfer-accounting', help='Ожидаемые стороны перевода по одной исходной записи')
    transfer_accounting.add_argument('input', type=Path)
    transfer_accounting.add_argument('--accounts', type=Path, required=True)
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
    for command in (debit, bank, api, backup, compare, review, classification, movement, resolve, accounting,
                    verify, restore, prepare, register, transfers, transfer_accounting, confirmations):
        command.add_argument('--output', type=Path, help='Новый JSON-файл, существующий не перезаписывается')
    return result


def normalize(args: argparse.Namespace) -> dict:
    if args.adapter == 'plan-confirmations':
        content = args.input.read_bytes()
        comparison = json.loads(content.decode('utf-8-sig'))
        result = plan_confirmations(comparison)
        return {**result, 'adapter': 'confirmation-plan', 'records': result['decisions'],
                'coverage': comparison['coverage'],
                'issues': [r for r in result['decisions'] if r['action'] == 'requires_review'],
                'comparison_sha256': hashlib.sha256(content).hexdigest()}
    if args.adapter == 'transfer-accounting':
        content, accounts = args.input.read_bytes(), args.accounts.read_bytes()
        result = expand_transfers(json.loads(content.decode('utf-8-sig')),
                                  json.loads(accounts.decode('utf-8-sig')))
        return {**result, 'transfer_inputs_sha256': {
            'source': hashlib.sha256(content).hexdigest(),
            'accounts': hashlib.sha256(accounts).hexdigest()}}
    if args.adapter == 'plan-transfers':
        return transfer_snapshot(args)
    if args.adapter == 'movement-control':
        return movement_snapshot(args)
    if args.adapter == 'classification-evidence':
        content = args.input.read_bytes()
        result = add_classification_evidence(json.loads(content.decode('utf-8-sig')))
        return {**result, 'classification_input_sha256': hashlib.sha256(content).hexdigest()}
    if args.adapter == 'yandex-accounting':
        content = args.input.read_bytes()
        result = expand_accounting(json.loads(content.decode('utf-8-sig')),
                                    parent_window_seconds=args.parent_window_seconds)
        return {**result, 'accounting_input_sha256': hashlib.sha256(content).hexdigest()}
    if args.adapter == 'resolve-source':
        content, specs = args.input.read_bytes(), args.resolutions.read_bytes()
        result = apply_resolutions(json.loads(content.decode('utf-8-sig')),
                                   json.loads(specs.decode('utf-8-sig')))
        return {**result, 'resolution_inputs_sha256': {
            'source': hashlib.sha256(content).hexdigest(),
            'resolutions': hashlib.sha256(specs).hexdigest()}}
    if args.adapter == 'review':
        content = args.input.read_bytes()
        result = build_review(json.loads(content.decode('utf-8-sig')))
        return {**result, 'comparison_sha256': hashlib.sha256(content).hexdigest()}
    if args.adapter == 'register-backup':
        if args.output and (args.output.exists() or args.output.is_symlink()):
            raise FileExistsError('Отчёт должен быть новым отдельным файлом')
        report = json.loads(args.report.read_text(encoding='utf-8-sig'))
        return register_backup(args.input, report, args.preferences, args.source_filename,
                               datetime.fromisoformat(args.timestamp), args.registry_archive)
    if args.adapter == 'prepare-backup':
        if args.output and (args.output.exists() or args.output.resolve() == args.target.resolve()):
            raise FileExistsError('Отчёт должен быть новым отдельным файлом')
        plan = extract_backup_plan(json.loads(args.plan.read_text(encoding='utf-8-sig')))
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
    # Пропавшие операции считаются только по полностью прочитанному периоду.
    period = source.get('period') if source.get('coverage') == 'snapshot_complete' else None
    result = compare_records(source['records'], ledger['records'], mapping,
                             window_seconds=args.window_seconds, source_period=period)
    return {**result, 'input_count': source['input_count'], 'rejected': source['rejected'],
            'source_input': {key: value for key, value in source.items() if key != 'records'},
            'ledger_input': {key: value for key, value in ledger.items() if key != 'records'},
            'inputs_sha256': {'source': source_hash, 'ledger': ledger_hash,
                              'accounts': hashlib.sha256(map_bytes).hexdigest()}}


def movement_snapshot(args: argparse.Namespace) -> dict:
    source, source_hash = read_snapshot(args.input)
    ledger, ledger_hash = read_snapshot(args.ledger)
    map_bytes = args.accounts.read_bytes()
    mapping = json.loads(map_bytes.decode('utf-8-sig'))
    zone = ZoneInfo(args.timezone)
    start = datetime.combine(date.fromisoformat(args.from_date), time.min, zone)
    end = datetime.combine(date.fromisoformat(args.to_date) + timedelta(days=1), time.min, zone)
    result = compare_movements(source, ledger, mapping, start, end)
    return {**result, 'movement_inputs_sha256': {'source': source_hash, 'ledger': ledger_hash,
                                               'accounts': hashlib.sha256(map_bytes).hexdigest()}}


def transfer_snapshot(args: argparse.Namespace) -> dict:
    source, source_hash = read_snapshot(args.input)
    fingerprint = backup_fingerprint(args.backup)
    ledger = read_backup(args.backup, args.timezone)
    map_bytes = args.accounts.read_bytes()
    mapping = json.loads(map_bytes.decode('utf-8-sig'))
    zone = ZoneInfo(args.timezone)
    start = datetime.combine(date.fromisoformat(args.from_date), time.min, zone)
    end = datetime.combine(date.fromisoformat(args.to_date) + timedelta(days=1), time.min, zone)
    result = build_transfer_plan(source, ledger, mapping, start, end,
        pair_window_seconds=args.pair_window_seconds, ledger_window_seconds=args.ledger_window_seconds)
    if backup_fingerprint(args.backup) != fingerprint:
        raise ValueError('Исходная резервная копия изменилась во время подготовки плана')
    plan = ({'schema_version': 1, 'source_sha256': fingerprint, 'updates': [],
             'additions': result['additions']} if result['additions'] else None)
    return {**result, 'backup_plan': plan, 'backup_records': ledger['input_count'],
            'inputs_sha256': {'source': source_hash, 'backup': fingerprint,
                              'accounts': hashlib.sha256(map_bytes).hexdigest()}}


def extract_backup_plan(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('Нужен объект плана')
    if value.get('adapter') != 'transfer-plan':
        return value
    plan = value.get('backup_plan')
    if (not isinstance(plan, dict) or not isinstance(plan.get('additions'), list)
            or type(value.get('selected_pair_count')) is not int
            or plan.get('additions') != value.get('additions')
            or len(plan.get('additions', [])) != 2 * value.get('selected_pair_count', 0)
            or plan.get('source_sha256') != value.get('inputs_sha256', {}).get('backup')):
        raise ValueError('Отчёт не содержит согласованного непустого плана')
    return plan


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
