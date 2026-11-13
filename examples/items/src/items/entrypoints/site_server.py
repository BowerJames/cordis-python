# A small items API with discovery: `uv run site-example --port 8080`.
# Items are kept in memory; `SITE_DATABASE=site.sqlite uv run site-example` keeps them in SQLite, from one run to the next.
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence

from cordis import open_runtime
from cordis_loader import Entry, Loader, patched, without
from cordis_plugins import (
    StopOnFailure,
    UsageError,
    argparse_args,
    argument_table,
    asyncio_http_server,
    get_env_os,
    route_table,
    sqlite_database,
    sqlite_sql,
    stdio,
    stop_on_signals,
)
from items.components import discovery, items_api, items_store, routes_command, server_cli, sql_items_store

# The entry that parses the command line, loaded after every other.
ARGS_ENTRY = "args"

def compose(argv: Sequence[str] | None = None, database: str | Path | None = None) -> list[Entry]:
    entries = [
        Entry("stdio", stdio),
        Entry("env", get_env_os),
        Entry("cli", argument_table),
        Entry("routes", route_table),
        Entry("server", server_cli, config={"server": asyncio_http_server}),
        Entry("routes-command", routes_command),
        Entry("items", items_store),
        Entry("items-api", items_api),
        Entry("discovery", discovery),
        Entry(ARGS_ENTRY, argparse_args, config={"argv": argv, "prog": "site"}),
    ]
    if database is not None:
        # Kept in SQLite instead: the store entry is replaced like for like, and nothing that uses items changes.
        entries = patched(entries, Entry("items", sql_items_store), Entry("sqlite", sqlite_database, config={"path": database}),
                          Entry("sql", sqlite_sql))
    return entries

async def run(entries: list[Entry]) -> int:
    failed = StopOnFailure()
    async with open_runtime(on_error=failed) as runtime:
        with stop_on_signals(runtime):
            loader = Loader(runtime.root)
            # A command line is parsed whole, so its parser is loaded once everything else has settled, when every
            # component that will register an option has.
            await loader.load(without(entries, ARGS_ENTRY))
            await runtime.settled()
            await loader.load(entries)
            # The server, loaded once the options are parsed, holds the runtime until a signal or a failure asks it to
            # stop. Asked only for help or the routes, no server is loaded, and the run is done once that is printed.
            await runtime.done()
    if failed.of(UsageError):
        return 2
    for _, error in failed.failures:
        notes = "; ".join(getattr(error, "__notes__", []))
        sys.stderr.write(f"site: {error}" + (f" ({notes})" if notes else "") + "\n")
    return failed.status()

def main() -> None:
    sys.exit(asyncio.run(run(compose(database=os.environ.get("SITE_DATABASE")))))

if __name__ == "__main__":
    main()
