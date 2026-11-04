import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator, Callable

from cordis import Component, Context, Inverse

from ..contracts import SQLITE, SqliteDatabase, SqliteRun


def sqlite_database(path: str | Path = ":memory:") -> Component:
    # Owns one connection to the database at path: opened as the component loads, closed as it unloads, and only ever
    # used from a thread of its own, one piece of work at a time.
    chosen = str(path)

    async def apply(ctx: Context) -> AsyncIterator[Inverse]:
        loop = asyncio.get_running_loop()
        thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        try:
            # Autocommit, so whoever runs a transaction begins and ends it.
            connection = await loop.run_in_executor(thread, lambda: sqlite3.connect(chosen, isolation_level=None))
        except BaseException:
            thread.shutdown(wait=False)
            raise
        async def close() -> None:
            # Closed in its own thread, after whatever work is still running there.
            await loop.run_in_executor(thread, connection.close)
            thread.shutdown(wait=False)

        yield close
        lock = asyncio.Lock()
        holder: asyncio.Task[object] | None = None

        async def in_thread[T](work: Callable[[sqlite3.Connection], T]) -> T:
            # Work already running in the thread runs on if its caller is cancelled; what comes after waits for it.
            return await loop.run_in_executor(thread, work, connection)

        async def run[T](work: Callable[[sqlite3.Connection], T], /) -> T:
            if holder is not None and holder is asyncio.current_task():
                raise RuntimeError("this task holds the sqlite connection, so it runs its work through what hold gave "
                                   "it; waiting for the connection here would wait forever")
            async with lock:
                return await in_thread(work)

        @asynccontextmanager
        async def hold() -> AsyncGenerator[SqliteRun, None]:
            nonlocal holder
            if holder is not None and holder is asyncio.current_task():
                raise RuntimeError("this task already holds the sqlite connection; holding it again would wait forever")
            async with lock:
                holder = asyncio.current_task()
                held = True

                async def run_held[T](work: Callable[[sqlite3.Connection], T], /) -> T:
                    if not held:
                        raise RuntimeError("the sqlite connection has been let go; hold it again to run more work")
                    return await in_thread(work)

                try:
                    yield run_held
                finally:
                    held = False
                    holder = None

        ctx.provide(SQLITE, SqliteDatabase(path=chosen, run=run, hold=hold))

    return Component("sqlite database", apply=apply, provides=(SQLITE,))
