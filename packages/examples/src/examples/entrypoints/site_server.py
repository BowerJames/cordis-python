# A small items API with discovery: `uv run site-example --port 8080`.
# Items are kept in memory; `SITE_DATABASE=site.sqlite uv run site-example` keeps them in SQLite, from one run to the next.
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence

from cordis_loader import Entry, patched
from examples.site import discovery, items_api, items_store, routes_command, server_cli, sql_items_store
from plugins import (
    UsageError,
    argparse_cli,
    get_env_os,
    route_table,
    run_application,
    sqlite_database,
    sqlite_sql,
    stdio,
)


def compose(argv: Sequence[str] | None = None, database: str | Path | None = None) -> list[Entry]:
    entries = [
        Entry("stdio", stdio),
        Entry("env", get_env_os),
        Entry("cli", argparse_cli, config={"argv": argv, "prog": "site"}),
        Entry("routes", route_table),
        Entry("server", server_cli),
        Entry("routes-command", routes_command),
        Entry("items", items_store),
        Entry("items-api", items_api),
        Entry("discovery", discovery),
    ]
    if database is not None:
        # Kept in SQLite instead: the store entry is replaced like for like, and nothing that uses items changes.
        entries = patched(entries, Entry("items", sql_items_store), Entry("sqlite", sqlite_database, config={"path": database}),
                          Entry("sql", sqlite_sql))
    return entries

async def run(entries: list[Entry]) -> int:
    outcome = await run_application(entries)
    if outcome.failed_with(UsageError):
        return 2
    for _, error in outcome.failures:
        notes = "; ".join(getattr(error, "__notes__", []))
        sys.stderr.write(f"site: {error}" + (f" ({notes})" if notes else "") + "\n")
    return outcome.status

def main() -> None:
    sys.exit(asyncio.run(run(compose(database=os.environ.get("SITE_DATABASE")))))

if __name__ == "__main__":
    main()
