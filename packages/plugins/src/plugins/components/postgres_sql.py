import asyncio
import re
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from functools import lru_cache
from types import MappingProxyType
from typing import Any, AsyncGenerator, Awaitable, Callable

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from cordis import Component, Context

from ..contracts import POSTGRES, SQL, ConstraintViolation, Parameters, Row, Sql, SqlError

# What a statement without parameters is given.
_NONE: Parameters = MappingProxyType({})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")

type _Connection = PoolConnectionProxy[asyncpg.Record]

@lru_cache(maxsize=512)
def numbered(statement: str) -> tuple[str, tuple[str, ...]]:
    # The statement with each :name in it as asyncpg's $1, $2, ..., numbered in the order names first appear, and the
    # names in that order. What only looks like a parameter is left as it is: a ::cast, and anything inside quotes,
    # dollar quotes or comments. A $1 of its own is refused, as parameters are named.
    out: list[str] = []
    names: list[str] = []
    at, end = 0, len(statement)
    while at < end:
        char = statement[at]
        if char in "'\"":
            # E'...' strings escape with backslashes; every other quoted text by doubling its quote.
            backslashes = char == "'" and at > 0 and statement[at - 1] in "eE" and (
                at == 1 or not (statement[at - 2].isalnum() or statement[at - 2] == "_"))
            close = at + 1
            while close < end:
                if backslashes and statement[close] == "\\":
                    close += 2
                elif statement[close] == char:
                    if close + 1 < end and statement[close + 1] == char:
                        close += 2
                    else:
                        break
                else:
                    close += 1
            out.append(statement[at:close + 1])
            at = close + 1
        elif statement.startswith("--", at):
            close = statement.find("\n", at)
            close = end if close == -1 else close
            out.append(statement[at:close])
            at = close
        elif statement.startswith("/*", at):
            # Block comments nest in Postgres.
            depth, close = 1, at + 2
            while close < end and depth:
                if statement.startswith("/*", close):
                    depth, close = depth + 1, close + 2
                elif statement.startswith("*/", close):
                    depth, close = depth - 1, close + 2
                else:
                    close += 1
            out.append(statement[at:close])
            at = close
        elif char == "$" and (quote := _DOLLAR_QUOTE.match(statement, at)) is not None:
            close = statement.find(quote.group(0), quote.end())
            close = end if close == -1 else close + len(quote.group(0))
            out.append(statement[at:close])
            at = close
        elif char == "$" and statement[at + 1:at + 2].isdigit():
            # Numbered among the names, it would mean two things at once.
            raise SqlError(f"the statement uses {statement[at:at + 2]}; parameters are named, as :name")
        elif statement.startswith("::", at):
            out.append("::")
            at += 2
        elif char == ":" and (name := _IDENTIFIER.match(statement, at + 1)) is not None:
            if name.group(0) not in names:
                names.append(name.group(0))
            out.append(f"${names.index(name.group(0)) + 1}")
            at = name.end()
        else:
            out.append(char)
            at += 1
    return "".join(out), tuple(names)

def _arguments(names: tuple[str, ...], parameters: Parameters) -> tuple[Any, ...]:
    missing = [name for name in names if name not in parameters]
    if missing:
        raise SqlError(f"the statement uses {', '.join(f':{name}' for name in missing)}, which it was not given")
    return tuple(parameters[name] for name in names)

def _changed(status: str) -> int:
    # Postgres says what a statement did as a command tag: INSERT 0 3, UPDATE 2, DELETE 1, CREATE TABLE, SELECT 5.
    command, *rest = status.split() or [""]
    if command in ("INSERT", "UPDATE", "DELETE", "MERGE") and rest and rest[-1].isdigit():
        return int(rest[-1])
    return 0

async def _refused_as_sql_errors[T](work: Awaitable[T]) -> T:
    # The contract's exceptions in place of asyncpg's, which are kept as the cause.
    try:
        return await work
    except asyncpg.IntegrityConstraintViolationError as error:
        raise ConstraintViolation(str(error)) from error
    except (asyncpg.PostgresError, asyncpg.InterfaceError) as error:
        raise SqlError(str(error)) from error

def _sql(connection: Callable[[], AbstractAsyncContextManager[_Connection]],
         transaction: Callable[[], AbstractAsyncContextManager[Sql]]) -> Sql:
    # Statements run on whatever connection gives: one from the pool, or the one a transaction holds.
    async def execute(statement: str, parameters: Parameters = _NONE, /) -> int:
        query, names = numbered(statement)
        arguments = _arguments(names, parameters)

        async def work() -> int:
            async with connection() as using:
                # Prepared, so it is one statement however it is written, and its command tag can be read back.
                prepared = await using.prepare(query)
                await prepared.fetch(*arguments)
                return _changed(prepared.get_statusmsg() or "")
        return await _refused_as_sql_errors(work())

    async def fetch(statement: str, parameters: Parameters = _NONE, /) -> tuple[Row, ...]:
        query, names = numbered(statement)
        arguments = _arguments(names, parameters)

        async def work() -> tuple[Row, ...]:
            async with connection() as using:
                return tuple(dict(record) for record in await using.fetch(query, *arguments))
        return await _refused_as_sql_errors(work())

    return Sql(execute=execute, fetch=fetch, transaction=transaction)

@asynccontextmanager
async def _transaction(connection: _Connection, lock: asyncio.Lock) -> AsyncGenerator[Sql, None]:
    # A transaction on a connection held for it; asyncpg makes one opened inside another a savepoint. The lock keeps
    # its statements one at a time, as a connection runs only one at once, whoever in the transaction sends them.
    transaction = connection.transaction()
    async with lock:
        await _refused_as_sql_errors(transaction.start())
    is_open = True

    @asynccontextmanager
    async def while_open() -> AsyncGenerator[_Connection, None]:
        if not is_open:
            raise RuntimeError("this transaction has ended; open another to run more statements")
        async with lock:
            yield connection

    try:
        yield _sql(while_open, lambda: _transaction(connection, lock))
    except BaseException:
        is_open = False
        # What made it fail carries on even if rolling back fails too: the pool resets a connection it is given back.
        with suppress(asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
            async with lock:
                await transaction.rollback()
        raise
    is_open = False
    async with lock:
        await _refused_as_sql_errors(transaction.commit())

def postgres_sql() -> Component:
    # The SQL contract over a Postgres pool. Each statement outside a transaction takes a connection for itself, and
    # each transaction holds one until it ends, so transactions run side by side, each seeing only what is committed.
    def apply(ctx: Context) -> None:
        pool = ctx.get(POSTGRES)
        # The tasks holding a transaction, refused the pool's own statements as the contract says: on a database
        # with one connection, they would wait for themselves forever.
        holding: set[asyncio.Task[Any]] = set()

        def refuse_holders() -> None:
            if asyncio.current_task() in holding:
                raise RuntimeError("this task holds a transaction, so it runs its statements through the transaction's "
                                   "Sql; on a database with one connection, the database's own would wait forever")

        @asynccontextmanager
        async def from_pool() -> AsyncGenerator[_Connection, None]:
            refuse_holders()
            async with pool.acquire() as connection:
                yield connection

        @asynccontextmanager
        async def transaction() -> AsyncGenerator[Sql, None]:
            refuse_holders()
            task = asyncio.current_task()
            assert task is not None
            async with pool.acquire() as connection:
                holding.add(task)
                try:
                    async with _transaction(connection, asyncio.Lock()) as sql:
                        yield sql
                finally:
                    holding.discard(task)

        ctx.provide(SQL, _sql(from_pool, transaction))

    return Component("postgres sql", apply=apply, requires=(POSTGRES,), provides=(SQL,))
