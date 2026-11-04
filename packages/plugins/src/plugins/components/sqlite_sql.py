import sqlite3
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import MappingProxyType
from typing import AsyncGenerator, Awaitable, Callable

from cordis import Component, Context

from ..contracts import SQL, SQLITE, ConstraintViolation, Parameters, Row, Sql, SqlError, SqliteRun

# What a statement without parameters is given.
_NONE: Parameters = MappingProxyType({})

async def _refused_as_sql_errors[T](work: Awaitable[T]) -> T:
    # The contract's exceptions in place of sqlite3's, which are kept as the cause.
    try:
        return await work
    except sqlite3.IntegrityError as error:
        raise ConstraintViolation(str(error)) from error
    except sqlite3.Error as error:
        raise SqlError(str(error)) from error

def _sql(run: SqliteRun, transaction: Callable[[], AbstractAsyncContextManager[Sql]]) -> Sql:
    # Statements run through run; sqlite3 takes :name parameters as they are.
    async def execute(statement: str, parameters: Parameters = _NONE, /) -> int:
        def work(connection: sqlite3.Connection) -> int:
            # sqlite3 counts -1 for statements that change no rows by their nature, such as CREATE or SELECT.
            return max(connection.execute(statement, dict(parameters)).rowcount, 0)
        return await _refused_as_sql_errors(run(work))

    async def fetch(statement: str, parameters: Parameters = _NONE, /) -> tuple[Row, ...]:
        def work(connection: sqlite3.Connection) -> tuple[Row, ...]:
            cursor = connection.execute(statement, dict(parameters))
            columns = [column[0] for column in cursor.description or ()]
            return tuple(dict(zip(columns, row)) for row in cursor.fetchall())
        return await _refused_as_sql_errors(run(work))

    return Sql(execute=execute, fetch=fetch, transaction=transaction)

@asynccontextmanager
async def _transaction(run: SqliteRun, depth: int) -> AsyncGenerator[Sql, None]:
    # A transaction at depth 1, run on a held connection; deeper, a savepoint inside the one around it.
    name = f"savepoint_{depth}"
    begin, commit, rollback = (
        ("BEGIN", "COMMIT", ("ROLLBACK",)) if depth == 1
        else (f"SAVEPOINT {name}", f"RELEASE {name}", (f"ROLLBACK TO {name}", f"RELEASE {name}"))
    )

    def statement(text: str) -> Callable[[sqlite3.Connection], None]:
        def work(connection: sqlite3.Connection) -> None:
            connection.execute(text)
        return work

    async def roll_back() -> None:
        for text in rollback:
            await _refused_as_sql_errors(run(statement(text)))

    await _refused_as_sql_errors(run(statement(begin)))
    is_open = True

    async def run_while_open[T](work: Callable[[sqlite3.Connection], T], /) -> T:
        if not is_open:
            raise RuntimeError("this transaction has ended; open another to run more statements")
        return await run(work)

    try:
        yield _sql(run_while_open, lambda: _transaction(run_while_open, depth + 1))
    except BaseException:
        is_open = False
        await roll_back()
        raise
    is_open = False
    try:
        await _refused_as_sql_errors(run(statement(commit)))
    except BaseException:
        # A commit SQLite refused leaves the transaction open, so it is rolled back rather than left holding locks.
        await roll_back()
        raise

def sqlite_sql() -> Component:
    # The SQL contract over a SQLite connection. A transaction holds the connection until it ends, so statements
    # outside it wait, and only ever see it before it began or after it ended.
    def apply(ctx: Context) -> None:
        database = ctx.get(SQLITE)

        @asynccontextmanager
        async def transaction() -> AsyncGenerator[Sql, None]:
            async with database.hold() as held, _transaction(held, 1) as sql:
                yield sql

        ctx.provide(SQL, _sql(database.run, transaction))

    return Component("sqlite sql", apply=apply, requires=(SQLITE,), provides=(SQL,))
