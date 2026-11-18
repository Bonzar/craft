"""Per-store execution ownership, checked inside each publication transaction.

This is independent of frozen evidence and causal run context. An owned older
snapshot may still publish history; the existing current-pointer fences decide
whether it can replace a newer answer.
"""
from collections.abc import Callable,Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import sqlite3
from typing import Any

from .queue import LeaseLost


class GuardLeaseLost(LeaseLost):
    """Execution ownership was not proven; never a semantic failure/notice."""


_GUARDS: ContextVar[tuple[tuple[Any,Callable],...]]=ContextVar('memory_publication_guards',default=())


@contextmanager
def bind_guard(engine,check: Callable[[sqlite3.Connection],object]) -> Iterator[None]:
    """Compose a synchronous captured-claim check for this exact engine store."""
    if not callable(check):raise TypeError('publication guard must be callable')
    token=_GUARDS.set((*_GUARDS.get(),(engine.store,check)))
    try:yield
    finally:_GUARDS.reset(token)


def check_guard(engine,db: sqlite3.Connection) -> None:
    """Fail closed before publication, using the owner's SAME write transaction."""
    checks=[check for store,check in _GUARDS.get() if store is engine.store]
    if not checks:return
    if db is not engine.store._connection or not db.in_transaction or not engine.store._writing:
        raise GuardLeaseLost('publication guard requires the same owned write transaction')
    for check in checks:
        try:
            if check(db) is False:raise GuardLeaseLost('publication execution lease is no longer owned')
        except GuardLeaseLost:raise
        except Exception as error:
            # Queue, derived and naming leases use different exception classes.
            # Failed ownership proof must not become a published model failure.
            raise GuardLeaseLost('publication ownership check failed') from error
