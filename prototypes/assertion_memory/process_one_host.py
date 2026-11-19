"""Run one existing chat message against a paused memory server on this Mac."""
import argparse
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
from time import monotonic, sleep

from .mcp_server import ServiceConfig
from .model import processing_cancellation
from .process_one import process_one
from .process_one_report import collect_report
from .storage import MemoryStore
from .thought_processing import TracedThoughtWorker, _worker_signals
from .thought_service import ThoughtService


PAUSE_WAIT_SECONDS = 15


class ManualWorker(TracedThoughtWorker):
    @contextmanager
    def _attempt(self, operation, inputs, cancel_claim):
        with super()._attempt(operation, inputs, cancel_claim) as trace:
            self.message_run.trace(trace.id)
            yield trace


def _loaded_pid(label):
    result = subprocess.run(['launchctl', 'print', label], capture_output=True, text=True)
    match = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.M)
    return int(match[1]) if result.returncode == 0 and match else None


def _pause_receipt(directory, config):
    receipt = json.loads((directory / 'processing-pause.json').read_text())
    expected_db = hashlib.sha256(config.db.encode()).hexdigest()
    if receipt.get('state') != 'paused' or receipt.get('database_identity') != expected_db:
        raise RuntimeError('The queue pause does not match this database')
    return receipt['holder_pid']


def _pause_agent(directory):
    program = (directory / 'hold-processing-pause.py').resolve()
    root = Path.home() / 'Library/LaunchAgents'
    matches = []
    for path in root.glob('local.assertion-memory.processing-pause.*.plist'):
        spec = plistlib.loads(path.read_bytes())
        argv = spec.get('ProgramArguments') or []
        if spec.get('Label') == path.stem and len(argv) >= 2 and Path(argv[1]).resolve() == program:
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeError('Cannot identify one matching queue pause LaunchAgent')
    path, = matches
    domain = f'gui/{os.getuid()}'
    return domain, f'{domain}/{path.stem}', path


def _restore(directory, config, agent, previous):
    domain, label, path = agent
    state = subprocess.run(['launchctl', 'bootstrap', domain, str(path)], capture_output=True, text=True)
    deadline = monotonic() + PAUSE_WAIT_SECONDS
    while monotonic() < deadline:
        pid = _pause_receipt(directory, config)
        if pid != previous and pid == _loaded_pid(label):
            return
        sleep(0.2)
    raise RuntimeError(f'Queue pause was not restored. {state.stderr.strip()}')


@contextmanager
def _borrow_pause(directory, config):
    with (directory / 'process-one.lock').open('a') as local_lock:
        try:
            fcntl.flock(local_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('A one-message command is already running') from error
        old_pid = _pause_receipt(directory, config)
        agent = _pause_agent(directory)
        if _loaded_pid(agent[1]) != old_pid:
            raise RuntimeError('The reported pause holder is absent')
        _live_config(directory, expected=config)
        subprocess.run(['launchctl', 'bootout', agent[1]], check=True, capture_output=True, text=True)
        try:
            with open(config.db + '.worker.lock', 'a') as lock:
                deadline = monotonic() + PAUSE_WAIT_SECONDS
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as error:
                        if monotonic() >= deadline:
                            raise RuntimeError('Could not take the existing worker lock') from error
                        sleep(0.2)
                    else:
                        break
                yield
        finally:
            _restore(directory, config, agent, old_pid)


def _live_config(directory, *, expected=None):
    receipt = json.loads((directory / 'deployment.json').read_text())
    config = ServiceConfig(**json.loads(Path(receipt['config']).read_text())['service'])
    if config.processing_session_ids != ():
        raise RuntimeError('Pause background processing before a one-message run')
    if config.provider == 'none' or not Path(config.db).is_file():
        raise RuntimeError('The configured database and model are required')
    if expected is not None and config != expected:
        raise RuntimeError('The server processing configuration changed')
    return config


def _process(directory, config, session_id):
    with _worker_signals() as (pausing, cancelling), _borrow_pause(directory, config):
        if pausing():
            return {'status': 'interrupted'}
        with MemoryStore(config.db) as store, processing_cancellation(cancelling):
            service = replace(config, processing_session_ids=(session_id,)).service(store)
            if not isinstance(service, ThoughtService):
                raise RuntimeError('The current database does not use canonical thought processing')
            worker = ManualWorker(service.queue, service.engine, lock_owned=True,
                                  processing_session_ids=(session_id,), is_pausing=pausing)
            result = process_one(worker, session_id, collect=lambda item: collect_report(service, item))
    return {**result, 'background_processing': 'paused'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-dir', required=True, type=Path)
    parser.add_argument('--check', action='store_true', help='Verify the configured pause without processing')
    parser.add_argument('session_id', nargs='?')
    args = parser.parse_args(argv)
    if not args.check and not args.session_id:
        parser.error('session_id is required for processing')
    try:
        config = _live_config(args.server_dir)
        if args.check:
            checked = _loaded_pid(_pause_agent(args.server_dir)[1]) == _pause_receipt(args.server_dir, config)
            if not checked:
                raise RuntimeError('The reported pause holder is absent')
            result = {'status': 'ready', 'background_processing': 'paused'}
        else:
            result = _process(args.server_dir, config, args.session_id)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result = {'status': 'error', 'error': str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] in ('ready','completed','empty') else 1


if __name__ == '__main__':
    raise SystemExit(main())
