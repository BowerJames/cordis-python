# What every items store promises, whichever keeps the items; and what keeping them in SQL adds.
import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest

from cordis import Context
from cordis_loader import Entry
from examples.site import ITEMS, Item, Items, items_store, sql_items_store
from plugins import SQL, Sql, get_env_os, postgres_pool, postgres_sql, sqlite_database, sqlite_sql
from plugins.testing import run_scenario

STORES: dict[str, Callable[[Path], list[Entry]]] = {
    "in memory": lambda directory: [Entry("items", items_store)],
    "in sqlite": lambda directory: [Entry("sqlite", sqlite_database, config={"path": directory / "items.sqlite"}),
                                    Entry("sql", sqlite_sql), Entry("items", sql_items_store)],
}

# Postgres needs a server, so its runs are external; it is where transactions truly run side by side.
POSTGRES = pytest.param("in postgres", marks=[pytest.mark.external, pytest.mark.timeout(120)])

@pytest.fixture(params=[*STORES, POSTGRES])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> list[Entry]:
    if request.param == "in postgres":
        return [Entry("env", get_env_os), Entry("postgres", postgres_pool, config={"dsn": request.getfixturevalue("postgres_dsn")}),
                Entry("sql", postgres_sql), Entry("items", sql_items_store)]
    return STORES[request.param](tmp_path)

async def using(store: list[Entry], steps: Callable[[Items], Awaitable[None]]) -> None:
    async def go(ctx: Context) -> None:
        await steps(ctx.get(ITEMS))
    outcome = await run_scenario(store, go, requires=(ITEMS,))
    if outcome.failures:
        raise outcome.failures[0][1]

async def raw(path: Path, steps: Callable[[Sql], Awaitable[None]]) -> None:
    # The database as it is, without a store: for writing what an older store would have left.
    async def go(ctx: Context) -> None:
        await steps(ctx.get(SQL))
    outcome = await run_scenario([Entry("sqlite", sqlite_database, config={"path": path}), Entry("sql", sqlite_sql)],
                                 go, requires=(SQL,))
    if outcome.failures:
        raise outcome.failures[0][1]


async def test_items_are_numbered_from_one_in_the_order_they_are_added(store: list[Entry]) -> None:
    async def steps(items: Items) -> None:
        assert [await items.add(name) for name in ("pen", "ink", "paper")] == [
            Item(id=1, name="pen"), Item(id=2, name="ink"), Item(id=3, name="paper")]
        assert await items.get(2) == Item(id=2, name="ink")
    await using(store, steps)

async def test_a_number_never_given_out_has_no_item(store: list[Entry]) -> None:
    async def steps(items: Items) -> None:
        await items.add("pen")
        assert await items.get(2) is None
        assert await items.get(0) is None
    await using(store, steps)

async def test_items_added_at_once_each_get_a_number_of_their_own(store: list[Entry]) -> None:
    async def steps(items: Items) -> None:
        added = await asyncio.gather(*(items.add(f"item {n}") for n in range(10)))
        assert sorted(item.id for item in added) == list(range(1, 11))
    await using(store, steps)

async def test_a_database_with_items_from_before_the_counter_numbers_on_from_them(tmp_path: Path) -> None:
    # A file written when items numbered themselves by counting: the counter starts from the highest number there.
    path = tmp_path / "older.sqlite"

    async def write(sql: Sql) -> None:
        await sql.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        await sql.execute("INSERT INTO items (id, name) VALUES (1, 'pen'), (5, 'ink')")

    found: list[Item | None] = []

    async def read(items: Items) -> None:
        found.append(await items.get(5))
        found.append(await items.add("paper"))

    await raw(path, write)
    await using([Entry("sqlite", sqlite_database, config={"path": path}), Entry("sql", sqlite_sql),
                 Entry("items", sql_items_store)], read)
    assert found == [Item(id=5, name="ink"), Item(id=6, name="paper")]

async def test_items_kept_in_a_sqlite_file_are_there_on_the_next_run(tmp_path: Path) -> None:
    stored = [Entry("sqlite", sqlite_database, config={"path": tmp_path / "kept.sqlite"}), Entry("sql", sqlite_sql),
              Entry("items", sql_items_store)]

    async def first(items: Items) -> None:
        await items.add("pen")

    found: list[Item | None] = []

    async def second(items: Items) -> None:
        found.append(await items.get(1))
        found.append(await items.add("ink"))

    await using(stored, first)
    await using(stored, second)
    assert found == [Item(id=1, name="pen"), Item(id=2, name="ink")]
