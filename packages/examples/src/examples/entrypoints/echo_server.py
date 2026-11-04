# An echo server: `uv run echo-server`, then `uv run curl-example http://127.0.0.1:8000/echo -d hello`. Ctrl-C to stop.
# `ECHO_SERVER=fastapi uv run echo-server` serves the same routes with FastAPI instead.
import asyncio
import os
import sys
from typing import Literal

from cordis_loader import Entry
from examples.echo import announce, echo, health
from plugins import (
    Outcome,
    asyncio_http_server,
    describe,
    fastapi_http_server,
    get_env_os,
    route_table,
    run_application,
    stdio,
)

Server = Literal["asyncio", "fastapi"]

def compose(port: int | None = None, server: Server = "asyncio") -> list[Entry]:
    # The routes live in a table of their own, so the server entry can be either provider, and either can be swapped
    # for the other while the application runs, without any route noticing.
    return [
        Entry("stdio", stdio),
        Entry("env", get_env_os),
        Entry("routes", route_table),
        Entry("server", fastapi_http_server if server == "fastapi" else asyncio_http_server, config={"port": port}),
        Entry("health", health),
        Entry("echo", echo),
        Entry("announce", announce),
    ]

def chosen_server() -> Server:
    chosen = os.environ.get("ECHO_SERVER", "asyncio")
    if chosen not in ("asyncio", "fastapi"):
        sys.exit(f"echo-server: ECHO_SERVER must be asyncio or fastapi, not {chosen!r}")
    return "fastapi" if chosen == "fastapi" else "asyncio"

def report(prog: str, outcome: Outcome) -> int:
    for _, error in outcome.failures:
        notes = "; ".join(getattr(error, "__notes__", []))
        sys.stderr.write(f"{prog}: {error}" + (f" ({notes})" if notes else "") + "\n")
    return outcome.status

async def run(entries: list[Entry]) -> int:
    return report("echo-server", await run_application(entries))

def main() -> None:
    if "--describe" in sys.argv[1:]:
        print(describe(compose(server=chosen_server())))
        return
    sys.exit(asyncio.run(run(compose(server=chosen_server()))))

if __name__ == "__main__":
    main()
