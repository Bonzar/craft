"""Generate owned launchd manifests and take online, no-clobber SQLite backups.

This command never loads/unloads a LaunchAgent, changes a tunnel, or signals a PID.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import shutil
import stat
import sys
import tempfile
from .backup import snapshot_database


def _absolute(value):
    path=Path(value)
    if not path.is_absolute():raise ValueError('an absolute path is required')
    return path


def _base(identity, *, python, repository, log_dir, kind='http'):
    python=_absolute(python);repository=_absolute(repository).resolve(strict=True)
    log_dir=_absolute(log_dir)
    label='local.assertion-memory.'+kind+'.'+hashlib.sha256(identity.encode()).hexdigest()[:20]
    paths=[str(python.parent),'/opt/homebrew/bin','/usr/local/bin','/usr/bin','/bin']
    codex=shutil.which('codex')
    if codex:paths.insert(0,str(Path(codex).parent))
    return {'Label':label,'WorkingDirectory':str(repository),
        'EnvironmentVariables':{'PYTHONPATH':str(repository),'PYTHONUNBUFFERED':'1','PATH':os.pathsep.join(dict.fromkeys(paths))},
        'ProcessType':'Background','Umask':0o077,
        'StandardOutPath':str(log_dir/(label+'.stdout.log')),
        'StandardErrorPath':str(log_dir/(label+'.stderr.log'))},str(python)


def server_manifest(config, *, python=sys.executable, repository, log_dir):
    config=_absolute(config).resolve(strict=True)
    result,python=_base(str(config),python=python,repository=repository,log_dir=log_dir)
    result.update(ProgramArguments=[python,'-m','prototypes.assertion_memory.http_server','--config',str(config)],
                  RunAtLoad=True,KeepAlive=True,ThrottleInterval=10,ExitTimeOut=30)
    return result


def backup_manifest(source, backup_dir, *, python=sys.executable, repository, log_dir,
                    ledger_source=None, hour=3, minute=15):
    if type(hour) is not int or not 0<=hour<=23 or type(minute) is not int or not 0<=minute<=59:
        raise ValueError('invalid daily calendar time')
    source=_absolute(source).resolve(strict=True);backup_dir=_absolute(backup_dir)
    result,python=_base('backup:'+str(source),python=python,repository=repository,log_dir=log_dir,kind='backup')
    args=[python,'-m','prototypes.assertion_memory.server_operations','backup','--source',str(source),'--backup-dir',str(backup_dir)]
    if ledger_source is not None:args+=['--ledger-source',str(_absolute(ledger_source).resolve(strict=True))]
    result.update(ProgramArguments=args,RunAtLoad=False,StartCalendarInterval={'Hour':hour,'Minute':minute})
    return result


def _private_log(path):
    directory=path.parent
    directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    info=directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ValueError('log directory must be owner-only (700) and owned by this user')
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():raise ValueError('log must be an owned regular file')
        os.fchmod(fd,0o600)
    finally:os.close(fd)


def write_manifest(manifest, output_dir):
    output_dir=_absolute(output_dir);output_dir.mkdir(parents=True,mode=0o700,exist_ok=True)
    for key in ('StandardOutPath','StandardErrorPath'):_private_log(Path(manifest[key]))
    path=output_dir/(manifest['Label']+'.plist');content=plistlib.dumps(manifest)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        info=path.lstat()
        if (path.is_symlink() or info.st_uid!=os.getuid() or info.st_mode&0o077
                or path.read_bytes()!=content):
            raise FileExistsError('existing manifest differs or is not private; refusing replacement')
    else:
        with os.fdopen(fd,'wb') as stream:stream.write(content)
    return path


def _ledger_snapshot(source, destination):
    source=source.resolve(strict=True)
    if destination.exists():raise FileExistsError('ledger destination already exists')
    descriptor,name=tempfile.mkstemp(prefix='.ledger-backup-',dir=destination.parent);os.close(descriptor)
    temporary=Path(name)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy);copy.execute('PRAGMA journal_mode=DELETE')
                if copy.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('ledger integrity check failed')
        with temporary.open('rb') as stream:os.fsync(stream.fileno())
        os.link(temporary,destination)  # Atomic publication that cannot replace an existing file.
    finally:temporary.unlink(missing_ok=True)


def backup(source, destination, *, ledger_source=None):
    source=_absolute(source);destination=_absolute(destination)
    ledger_destination=Path(str(destination)+'.model-usage.sqlite3') if ledger_source is not None else None
    if ledger_destination is not None:
        ledger_source=_absolute(ledger_source).resolve(strict=True)
        if ledger_destination.exists():raise FileExistsError('ledger destination already exists')
    snapshot_database(source,destination)
    # Deliberately separate: provider telemetry is not part of knowledge transactions.
    if ledger_source is not None:_ledger_snapshot(ledger_source,ledger_destination)
    return {'database':str(destination),'ledger':str(ledger_destination) if ledger_destination else None,
            'globally_consistent':False,'database_consistency':'sqlite_backup_snapshot',
            'ledger_consistency':'independent_sqlite_backup_snapshot' if ledger_destination else 'not_requested'}


def restore(source, destination):
    snapshot_database(_absolute(source),_absolute(destination))
    return {'restored':str(destination),'ledger_restored':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    for name in ('manifest','backup-manifest'):
        command=commands.add_parser(name)
        for flag in ('python','repository','log-dir','output-dir'):command.add_argument('--'+flag,type=Path,required=True)
        if name=='manifest':command.add_argument('--config',type=Path,required=True)
        else:
            command.add_argument('--source',type=Path,required=True);command.add_argument('--backup-dir',type=Path,required=True)
            command.add_argument('--ledger-source',type=Path);command.add_argument('--hour',type=int,default=3);command.add_argument('--minute',type=int,default=15)
    command=commands.add_parser('backup');command.add_argument('--source',type=Path,required=True)
    target=command.add_mutually_exclusive_group(required=True);target.add_argument('--destination',type=Path);target.add_argument('--backup-dir',type=Path)
    command.add_argument('--ledger-source',type=Path)
    command=commands.add_parser('restore');command.add_argument('--source',type=Path,required=True);command.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command in ('manifest','backup-manifest'):
            options={'python':args.python,'repository':args.repository,'log_dir':args.log_dir}
            manifest=(server_manifest(args.config,**options) if args.command=='manifest' else backup_manifest(args.source,args.backup_dir,ledger_source=args.ledger_source,hour=args.hour,minute=args.minute,**options))
            result={'manifest':str(write_manifest(manifest,args.output_dir)),'label':manifest['Label'],'installed':False}
        elif args.command=='backup':
            destination=args.destination or (_absolute(args.backup_dir)/('memory-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'.sqlite3'))
            result=backup(args.source,destination,ledger_source=args.ledger_source)
        else:result=restore(args.source,args.destination)
        print(json.dumps(result));return 0
    except (OSError,ValueError,sqlite3.Error) as error:parser.exit(1,'error: '+str(error)+'\n')

if __name__=='__main__':raise SystemExit(main())
