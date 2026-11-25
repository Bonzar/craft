"""Private admin primitives. The host must authenticate the owner before calling them.

This is not an MCP tool, ASGI middleware or general-purpose SQLite maintenance API.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

HEX = re.compile(r'[a-f0-9]{64}\Z')
TAG = re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?\Z')
NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,31}\Z')
CONFIRMATION_TTL = 90
MAX_CLIENTS = 16


class AdminError(ValueError):
    """Fixed code safe for a response; never contains supplied values."""


@dataclass(frozen=True)
class OwnerContext:
    """Server-derived identity only. Do not instantiate from request payload."""
    owner: str
    stage: str
    release: str
    generation: str

    def __post_init__(self):
        if (type(self.owner) is not str or not 1 <= len(self.owner) <= 200
                or self.stage not in ('test', 'prod')
                or type(self.release) is not str or not TAG.fullmatch(self.release)
                or type(self.generation) is not str or not HEX.fullmatch(self.generation)):
            raise AdminError('invalid_context')


def fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise AdminError('invalid_request')


def valid_name(name):
    if type(name) is not str or not NAME.fullmatch(name):
        raise AdminError('invalid_device_name')


def valid_digest(digest):
    if type(digest) is not str or not HEX.fullmatch(digest):
        raise AdminError('invalid_device_fingerprint')


class MachineRegistry:
    """Separate service-state ledger; deleting memory contents never revokes clients."""

    def __init__(self, directory, stage, static_clients=(), *, reserved=()):
        self.directory = Path(directory)
        if type(stage) is not str or stage not in ('test', 'prod'):
            raise AdminError('invalid_context')
        self.stage = stage
        self.static = dict(static_clients)
        self.reserved = frozenset(reserved) | frozenset(self.static.values())
        for name, digest in self.static.items():
            valid_name(name)
            valid_digest(digest)
        for digest in self.reserved:
            valid_digest(digest)
        if len(self.static) > MAX_CLIENTS or len(set(self.static.values())) != len(self.static):
            raise AdminError('invalid_registry')
        self.path = self.directory / f'visual-admin-devices-{stage}.sqlite3'
        self._init_lock = threading.Lock()

    def _connect(self):
        # State dir must be provisioned privately by the deployment, never a
        # shared cache, transport input, or location supplied by the browser.
        try:
            if self.directory.resolve(strict=True) != self.directory:
                raise AdminError('untrusted_registry')
            d = self.directory.lstat()
            if not stat.S_ISDIR(d.st_mode) or d.st_uid != os.getuid() or d.st_mode & 0o077:
                raise AdminError('untrusted_registry')
            with self._init_lock:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                try:
                    s = os.fstat(fd)
                    if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                            or s.st_nlink != 1 or s.st_mode & 0o077):
                        raise AdminError('untrusted_registry')
                finally:
                    os.close(fd)
            for suffix in ('-journal', '-wal', '-shm'):
                sidecar = Path(str(self.path) + suffix)
                if not os.path.lexists(sidecar):
                    continue
                s = sidecar.lstat()
                if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                        or s.st_nlink != 1 or s.st_mode & 0o077):
                    raise AdminError('untrusted_registry')
            c = sqlite3.connect(self.path, timeout=2, isolation_level=None)
            try:
                c.execute('CREATE TABLE IF NOT EXISTS devices ('
                          'name TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, '
                          'created_at INTEGER NOT NULL)')
                c.execute('CREATE TABLE IF NOT EXISTS events ('
                          'id INTEGER PRIMARY KEY, action TEXT NOT NULL, created_at INTEGER NOT NULL)')
                return c
            except BaseException:
                c.close()
                raise
        except (OSError, sqlite3.Error) as exc:
            raise AdminError('registry_unavailable') from exc

    def _rows(self, c):
        rows = c.execute('SELECT name,fingerprint,created_at FROM devices ORDER BY name LIMIT ?', (MAX_CLIENTS + 1,)).fetchall()
        if len(rows) + len(self.static) > MAX_CLIENTS:
            raise AdminError('invalid_registry')
        for name, digest, created_at in rows:
            valid_name(name)
            valid_digest(digest)
            if name in self.static or digest in self.reserved or type(created_at) is not int:
                raise AdminError('invalid_registry')
        return rows

    def list(self):
        with closing(self._connect()) as c:
            rows = self._rows(c)
        return [{'name': name, 'managed_by': 'host', 'revocable': False} for name in sorted(self.static)] + [
            {'name': name, 'managed_by': 'panel', 'revocable': True, 'created_at': created_at}
            for name, _, created_at in rows]

    def digests(self):
        """Call on each authorization; a revoked device fails its next request."""
        with closing(self._connect()) as c:
            return tuple(self.static.values()) + tuple(row[1] for row in self._rows(c))

    def _change(self, action, name, digest=None):
        valid_name(name)
        if name in self.static:
            raise AdminError('device_managed_by_host')
        if action == 'enroll':
            valid_digest(digest)
            if digest in self.reserved:
                raise AdminError('device_already_exists')
        with closing(self._connect()) as c:
            try:
                c.execute('BEGIN IMMEDIATE')
                rows = self._rows(c)
                stamp = int(time.time())
                if action == 'enroll':
                    if any(name == n or digest == d for n, d, _ in rows):
                        raise AdminError('device_already_exists')
                    if len(self.static) + len(rows) >= MAX_CLIENTS:
                        raise AdminError('device_limit')
                    c.execute('INSERT INTO devices VALUES (?,?,?)', (name, digest, stamp))
                elif c.execute('DELETE FROM devices WHERE name=?', (name,)).rowcount != 1:
                    raise AdminError('device_not_found')
                # Technical audit only, bounded; never old state, name or credential.
                c.execute('INSERT INTO events (action,created_at) VALUES (?,?)', (action, stamp))
                c.execute('DELETE FROM events WHERE id <= (SELECT max(id)-256 FROM events)')
                c.execute('COMMIT')
            except sqlite3.Error as exc:
                raise AdminError('registry_unavailable') from exc

    def enroll(self, name, digest):
        self._change('enroll', name, digest)

    def revoke(self, name):
        self._change('revoke', name)


class AdminOperations:
    def __init__(self, *, current_context, registry, reset=None, clock=time.monotonic):
        self.current_context = current_context
        self.registry = registry
        self.reset = reset
        self.clock = clock
        self._guard = threading.Lock()
        self._challenge = None
        self._busy = False

    def _require(self, context):
        # Current context is independently derived by the owner-authenticated
        # caller and verifies current release/generation, not a request header.
        if (type(context) is not OwnerContext or context.stage != self.registry.stage
                or context != self.current_context()):
            raise AdminError('context_changed')

    def devices(self, context):
        self._require(context)
        return {'stage': context.stage, 'devices': self.registry.list()}

    def enroll(self, context, data):
        fields(data, ('name', 'fingerprint'))
        with self._guard:
            self._require(context)
            if self._busy:
                raise AdminError('reset_busy')
            self.registry.enroll(data['name'], data['fingerprint'])
        return {'name': data['name'], 'enrolled': True}

    def revoke(self, context, data):
        fields(data, ('name',))
        with self._guard:
            self._require(context)
            if self._busy:
                raise AdminError('reset_busy')
            self.registry.revoke(data['name'])
        return {'name': data['name'], 'revoked': True}

    def prepare_reset(self, context, data):
        fields(data, ())
        with self._guard:
            self._require(context)
            if self.reset is None:
                raise AdminError('reset_unavailable')
            if self._busy:
                raise AdminError('reset_busy')
            challenge = secrets.token_hex(32)
            phrase = 'ОЧИСТИТЬ ' + context.stage.upper()
            self._challenge = (context, self.clock() + CONFIRMATION_TTL, challenge, phrase)
            return {'confirmation_id': challenge, 'phrase': phrase,
                    'expires_in': CONFIRMATION_TTL, 'stage': context.stage}

    def execute_reset(self, context, data):
        fields(data, ('confirmation_id', 'confirmation'))
        request_id = data['confirmation_id']
        confirmation = data['confirmation']
        if (type(request_id) is not str or not HEX.fullmatch(request_id)
                or type(confirmation) is not str or len(confirmation) > 80):
            raise AdminError('confirmation_invalid')
        with self._guard:
            self._require(context)
            if self.reset is None:
                raise AdminError('reset_unavailable')
            if self._busy:
                raise AdminError('reset_busy')
            item = self._challenge
            if (item is None or item[0] != context or self.clock() >= item[1]
                    or not hmac.compare_digest(request_id, item[2])
                    or not hmac.compare_digest(confirmation.encode('utf-8', errors='replace'), item[3].encode('utf-8'))):
                raise AdminError('confirmation_invalid')
            self._challenge = None
            self._busy = True
        try:
            result = self.reset(context)
            if result is not True:
                raise AdminError('reset_outcome_unconfirmed')
            return {'stage': context.stage, 'reset': True}
        except AdminError:
            raise
        except Exception as exc:
            raise AdminError('reset_outcome_unconfirmed') from exc
        finally:
            with self._guard:
                self._busy = False
