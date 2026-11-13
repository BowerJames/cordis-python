# An echo server: `uv run echo-server`, then `uv run curl-example http://127.0.0.1:8000/echo -d hello`. Ctrl-C to stop.
# `ECHO_SERVER=fastapi uv run echo-server` serves the same routes with FastAPI instead.
import asyncio
import os
import sys
from typing import Literal

from cordis import open_runtime
from cordis_loader import Entry, Loader, describe
from cordis_plugins import (
    StopOnFailure,
    announce,
    asyncio_http_server,
    fastapi_http_server,
    get_env_os,
    health,
    route_table,
    stdio,
    stop_on_signals,
)
from echo.components import echo

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

def report(prog: str, failed: StopOnFailure) -> int:
    for _, error in failed.failures:
        notes = "; ".join(getattr(error, "__notes__", []))
        sys.stderr.write(f"{prog}: {error}" + (f" ({notes})" if notes else "") + "\n")
    return failed.status()

async def run(entries: list[Entry]) -> int:
    # The server holds the runtime for as long as it serves, so this runs until a signal, or a failure, asks it to
    # stop; leaving the runtime's block then unloads everything, what relies on the server before the server.
    failed = StopOnFailure()
    async with open_runtime(on_error=failed) as runtime:
        with stop_on_signals(runtime):
            await Loader(runtime.root).load(entries)
            await runtime.done()
    return report("echo-server", failed)

def main() -> None:
    if "--describe" in sys.argv[1:]:
        print(describe(compose(server=chosen_server())))
        return
    sys.exit(asyncio.run(run(compose(server=chosen_server()))))

if __name__ == "__main__":
    main()
