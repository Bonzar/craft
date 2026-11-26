"""No-init OwnerStore reopen prototype. Legacy writer retirement is external."""
from collections import OrderedDict
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import stat
import threading

from .auth import OwnerStore

class ExistingOwnerStore(OwnerStore):
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'viewer.sqlite3'
        self._trusted()
        # Check shape and owner existence without reading passwords/sessions,
        # altering permissions, creating schema or migrating the database.
        with closing(self._connection('ro')) as db:
            db.execute('PRAGMA query_only=ON')
            if db.execute('SELECT 1 FROM owner WHERE id=1').fetchone() != (1,):
                raise ValueError('Existing browser owner not enrolled')
            db.execute('SELECT token,csrf,expires FROM sessions LIMIT 0')
            db.execute('SELECT id,body,created_at FROM bookmarks LIMIT 0')
        self.attempts = OrderedDict()
        self._attempt_lock = threading.Lock()

    def _trusted(self):
        uid=os.geteuid()
        if self.path.resolve(strict=True)!=self.path:
            raise ValueError('Noncanonical browser owner store')
        parent=self.directory.lstat()
        info=self.path.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=uid or stat.S_IMODE(parent.st_mode)!=0o700
                or not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o600):
            raise ValueError('Untrusted browser owner store')

    def _connection(self, mode):
        self._trusted()
        return sqlite3.connect(self.path.as_uri()+'?mode='+mode,uri=True,timeout=5)

    def connect(self):
        return self._connection('rw')

    def reset_graph_bookmarks(self, provider_id, epoch):
        # Adoption may read an already settled epoch, not create auxiliary
        # tables or remove a predecessor's bookmarks as a startup side effect.
        with closing(self._connection('ro')) as db:
            row=db.execute('SELECT epoch FROM bookmark_reset_epoch WHERE provider_id=?',
                           (provider_id,)).fetchone()
            if row!=(epoch,):
                raise ValueError('Browser bookmark epoch not independently settled')
