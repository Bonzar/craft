"""Публикация отдельной копии и добавление только её строки в BackupList macOS."""

import copy
import os
import plistlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .backup_edit import backup_fingerprint, validate_target
from .debit_credit_backup import read_backup

PREFERENCES_NAME = 'com.mr-brightside.debitandcreditMac.plist'
APP_PROCESS = 'Debit & Credit'


def app_is_running() -> bool:
    result = subprocess.run(['/usr/bin/pgrep', '-x', APP_PROCESS], capture_output=True)
    if result.returncode not in (0, 1):
        raise ValueError('Не удалось проверить состояние приложения')
    return result.returncode == 0


def read_preferences(path: Path) -> dict:
    value = plistlib.loads(path.read_bytes())
    if not isinstance(value, dict) or not isinstance(value.get('BackupList'), list):
        raise ValueError('Не найден реестр резервных копий')
    return value


def source_entry(preferences: dict, filename: str) -> dict:
    rows = preferences['BackupList']
    matches = [row for row in rows if row.get('fileName') == filename]
    if len(matches) != 1 or matches[0] != max(rows, key=lambda row: row['date']):
        raise ValueError('Исходная копия должна быть последней зарегистрированной копией')
    return matches[0]


def validate_registration(candidate: Path, report: dict, preferences: Path, filename: str,
                          stamp: datetime, archive: Path) -> tuple:
    if app_is_running():
        raise ValueError('Для регистрации приложение должно быть полностью закрыто')
    if preferences.name != PREFERENCES_NAME or archive.exists() or archive.is_symlink():
        raise ValueError('Нужны настройки Debit & Credit и новый файл архива реестра')
    before = read_preferences(preferences)
    entry = source_entry(before, filename)
    source = Path(entry['path'])
    data_folder = preferences.parent.parent.parent
    if source.parent != data_folder / 'Documents/backups' or source.name != filename:
        raise ValueError('Исходная копия находится вне папки резервных копий')
    if (not isinstance(report, dict) or report.get('adapter') != 'backup-preparation'
            or report.get('ready') is not True
            or report.get('source_sha256') != backup_fingerprint(source)
            or report.get('candidate_sha256') != backup_fingerprint(candidate)
            or report['candidate_sha256'].get('wal') is not None
            or report.get('source_records') != entry['number']):
        raise ValueError('Отчёт подготовки не соответствует файлам')
    if any(Path(str(candidate) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Подготовленная копия должна быть самостоятельным файлом без WAL')
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Время регистрации должно содержать часовой пояс')
    date = stamp.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    if date <= entry['date']:
        raise ValueError('Время регистрации должно быть позже исходной копии')
    snapshot = read_backup(candidate, str(stamp.tzinfo) if hasattr(stamp.tzinfo, 'key') else 'UTC')
    if not snapshot['ready'] or snapshot['input_count'] != report.get('candidate_records'):
        raise ValueError('Подготовленная копия не соответствует числу записей в отчёте')
    validate_attachments(entry, source.parent.parent / 'backups-attachments')
    target = source.parent / stamp.strftime('%Y-%m-%dT%H:%M:%S%z.dcbackup')
    validate_target(source, target)
    return before, entry, target, date


def validate_attachments(entry: dict, folder: Path) -> None:
    attachments = entry.get('attachments')
    if not isinstance(attachments, list):
        raise ValueError('Не задан список вложений исходной копии')
    for row in attachments:
        name = row.get('fileName')
        if not isinstance(name, str) or not name or Path(name).name != name or not (folder / name).is_file():
            raise ValueError('Архивный файл вложения отсутствует или имеет недопустимое имя')


def write_new(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)


def register_backup(candidate: Path | str, report: dict, preferences: Path | str,
                    source_filename: str, stamp: datetime, archive: Path | str) -> dict:
    candidate, preferences, archive = Path(candidate), Path(preferences), Path(archive)
    before, source, target, date = validate_registration(candidate, report, preferences,
                                                        source_filename, stamp, archive)
    entry = copy.deepcopy(source)
    entry.update(path=str(target), fileName=target.name, date=date, number=report['candidate_records'])
    if any(r.get('fileName') == target.name for r in before['BackupList']):
        raise ValueError('Копия уже зарегистрирована')
    write_new(archive, plistlib.dumps({'BackupList': before['BackupList']}, fmt=plistlib.FMT_BINARY))
    write_new(target, candidate.read_bytes())
    if backup_fingerprint(target) != report['candidate_sha256']:
        raise ValueError('Опубликованный файл отличается от подготовленного')
    if app_is_running() or read_preferences(preferences) != before:
        raise ValueError('Приложение открыто или настройки изменились до регистрации')
    if backup_fingerprint(Path(source['path'])) != report['source_sha256']:
        raise ValueError('Исходная копия изменилась до регистрации')
    command = ['/usr/bin/defaults', 'write', str(preferences.resolve().with_suffix('')), 'BackupList',
               '-array-add', plistlib.dumps(entry, fmt=plistlib.FMT_XML).decode()]
    result = subprocess.run(command, capture_output=True)
    after = read_preferences(preferences)
    expected = {**before, 'BackupList': [*before['BackupList'], entry]}
    issues = []
    if result.returncode != 0 or after != expected:
        issues.append({'code': 'registry_write_not_confirmed'})
    return {'schema_version': 1, 'adapter': 'backup-registration', 'input_count': 1,
            'records': [{'fileName': target.name, 'path': str(target), 'number': entry['number']}],
            'rejected': [], 'issues': issues, 'ready': not issues, 'coverage': 'single_registry_entry',
            'registry_archive': str(archive), 'candidate_sha256': backup_fingerprint(target),
            'app_list_visibility_verified': False, 'app_restore_verified': False,
            'limitations': ['macos_preferences_format_specific', 'does_not_restore_backup',
                            'do_not_retry_an_unconfirmed_write_without_reading_registry']}
