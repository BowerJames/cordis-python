import sqlite3
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from cordis import Key

# SQLite itself, for whatever needs more than the SQL contract, such as pragmas or backups: one connection, used from
# a thread of its own, as sqlite3 connections belong to the thread that opened them. The connection is in autocommit
# mode, so transactions are begun and ended by whoever runs them.

class SqliteRun(Protocol):
    # Runs work with the connection in its thread, one piece of work at a time, and gives back what work returns.
    def __call__[T](self, work: Callable[[sqlite3.Connection], T], /) -> Awaitable[T]: ...

@dataclass(slots=True, frozen=True)
class SqliteDatabase:
    # The file it is stored in, or :memory:.
    path: str
    # Waits while the connection is held by someone else; refuses from the task holding it, which would wait forever.
    run: SqliteRun
    # Holds the connection for a sequence of work, such as a transaction: nothing else runs on it until it is let go.
    # The SqliteRun it gives refuses work once it has been let go.
    hold: Callable[[], AbstractAsyncContextManager[SqliteRun]]

SQLITE: Key[SqliteDatabase] = Key("sqlite")
