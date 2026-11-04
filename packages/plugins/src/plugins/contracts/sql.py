from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from cordis import Key

# Every SQL provider keeps these promises, whatever database it talks to:
#   execute runs one statement and returns how many rows an INSERT, UPDATE or DELETE changed, or 0 for any other
#   statement; fetch runs one and returns every row it gives, each a mapping from column name to value, in order.
#   Parameters are named in the statement as :name and given as a mapping; every name a statement uses must be given.
#   None, int, float, str and bytes are stored and read back unchanged; other types are the provider's own business.
#   Outside a transaction each statement is committed on its own. transaction() opens one, and gives the Sql to run
#   its statements through: leaving it normally commits them all, leaving it by raising (cancellation included) rolls
#   them all back and the exception carries on. A transaction opened inside a transaction is a savepoint: its failure
#   rolls back only its own statements. Statements outside a transaction never see one partly done: they see the
#   database as it was before it began, or after it committed. A transaction's Sql refuses statements once it has
#   ended, and the database's own Sql refuses them from the task holding one of its transactions, as on a database
#   with a single connection that would wait for itself forever; both raise RuntimeError.
#   A statement that fails inside a transaction may leave the transaction unable to run any more, as on Postgres,
#   unless it ran inside a savepoint, whose rollback leaves the transaction as it was before.
#   A statement the database refuses raises SqlError, or ConstraintViolation when it breaks a constraint, such as a
#   unique key; never the driver's own exceptions, which are kept as the cause.
# What each provider decides for itself: the SQL it understands. Statements written to work on more than one database
# keep to what they share.

type Parameters = Mapping[str, Any]
type Row = Mapping[str, Any]

class SqlError(Exception):
    # A statement the database refused.
    pass

class ConstraintViolation(SqlError):
    # A statement that would break a constraint, such as a unique key or a NOT NULL column.
    pass

class Execute(Protocol):
    def __call__(self, statement: str, parameters: Parameters = ..., /) -> Awaitable[int]: ...

class Fetch(Protocol):
    def __call__(self, statement: str, parameters: Parameters = ..., /) -> Awaitable[tuple[Row, ...]]: ...

@dataclass(slots=True, frozen=True)
class Sql:
    execute: Execute
    fetch: Fetch
    transaction: Callable[[], AbstractAsyncContextManager["Sql"]]

SQL: Key[Sql] = Key("sql")
