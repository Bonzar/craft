#!/usr/bin/env python3
"""One detached polling worker per endpoint outbox; startup registers explicit paths."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outbox import Outbox
from lib.native_capture import poll
from lib.remote_client import Client
from lib.frontier import Coordinator


def run(binding, state, *, once=False, binding_path=None):
    capture = {**binding['capture'],'credential_env_var':binding.get('connection',{}).get('bearer_token_env_var')}
    root = Path(state)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'worker.lock').open('a+') as lock:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 0
        lock.seek(0); lock.truncate(); lock.write(str(os.getpid())); lock.flush()
        store = Outbox(root / 'outbox.sqlite3')
        client = Client(binding['connection'])
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, lambda *_: stop.set())
        coordinator = Coordinator(store, client)
        try:
            while not stop.is_set():
                if binding_path is not None:
                    try: current = json.loads(Path(binding_path).read_text())
                    except (OSError, ValueError): return 0
                    if current != binding: return 0
                for session in store.sessions():
                    if not session.get('watching', True): continue
                    if session.get('blocked'):
                        coordinator.observe(session['session_id'], {'status':'blocked'})
                        continue
                    try: observation = poll(store, session['session_id'], capture)
                    except FileNotFoundError: observation = {'status':'waiting_transcript'}
                    except Exception as failure:
                        # Keep malformed/replaced source gaps visible; never silently reset cursors.
                        current = store.session(session['session_id'])
                        current['blocked'] = type(failure).__name__
                        with store.db: store.save_session(current)
                        observation = {'status':'blocked'}
                    if observation.get('status') != 'inactive':
                        coordinator.observe(session['session_id'], observation)
                coordinator.tick()
                if once: return 0
                stop.wait(1.0)
        finally: store.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    return run(json.loads(args.binding.read_text()), args.state, once=args.once, binding_path=args.binding)

if __name__ == '__main__':
    raise SystemExit(main())
