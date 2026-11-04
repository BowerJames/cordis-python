import json
import socket
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import pytest

from cordis import Component, Context
from cordis_loader import Entry, patched
from cordis_plugins import (
    HTTP,
    SERVER_ADDRESS,
    STDERR,
    STDERR_IS_TERMINAL,
    STDOUT,
    STDOUT_IS_TERMINAL,
    Captured,
    Request,
    capturing_stdio,
    describe,
    httpx_http,
)
from cordis_plugins.testing import Steps, scenario
from examples.entrypoints.site_server import compose, run


def accepting(address: str) -> bool:
    # Whether the server would take a new connection right now; a blocking check, so it can run from a write.
    parts = urlsplit(address)
    assert parts.hostname is not None and parts.port is not None
    try:
        with socket.create_connection((parts.hostname, parts.port), timeout=0.2):
            return True
    except OSError:
        return False

def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port

def probing_stdio(seen: dict[str, Any], captured: Captured) -> Callable[[], Component]:
    # Captures stdout, and at the moment discovery says it has deregistered, records whether the server still accepts.
    def write(text: str) -> None:
        if text.startswith("deregistered"):
            seen["accepting"] = accepting(seen["address"])
        captured.stdout += text

    def apply(ctx: Context) -> None:
        ctx.provide(STDOUT, write)
        ctx.provide(STDERR, lambda text: None)
        ctx.provide(STDOUT_IS_TERMINAL, lambda: False)
        ctx.provide(STDERR_IS_TERMINAL, lambda: False)
    return lambda: Component("probing stdio", apply=apply, provides=(STDOUT, STDERR, STDOUT_IS_TERMINAL, STDERR_IS_TERMINAL))

async def site(argv: list[str], steps: Steps | None = None, database: Path | None = None,
               stdio: Callable[[], Component] | None = None) -> tuple[int, Captured]:
    capture, captured = capturing_stdio()
    entries = patched(compose(argv, database=database), Entry("stdio", stdio or capture))
    if steps is not None:
        entries = patched(entries, Entry("http", httpx_http),
                          Entry("scenario", lambda: scenario(steps, requires=(HTTP, SERVER_ADDRESS))))
    return await run(entries), captured


@pytest.mark.external
async def test_items_are_created_with_an_absolute_location_and_read_back() -> None:
    async def steps(ctx: Context) -> None:
        base, http = ctx.get(SERVER_ADDRESS), ctx.get(HTTP)
        created = await http.send(Request("POST", f"{base}/items", body=json.dumps({"name": "pen"}).encode()))
        assert (created.status, json.loads(created.body)) == (201, {"id": 1, "name": "pen"})
        location = created.header("location")
        assert location == f"{base}/items/1"
        read = await http.send(Request("GET", location))
        assert (read.status, json.loads(read.body)) == (200, {"id": 1, "name": "pen"})
        assert (await http.send(Request("POST", f"{base}/items", body=b"not json"))).status == 400
        assert (await http.send(Request("GET", f"{base}/items/9"))).status == 404
    status, _ = await site(["--port", "0"], steps)
    assert status == 0

@pytest.mark.external
async def test_discovery_reports_its_registration_while_running() -> None:
    async def steps(ctx: Context) -> None:
        base = ctx.get(SERVER_ADDRESS)
        status = await ctx.get(HTTP).send(Request("GET", f"{base}/discovery"))
        assert json.loads(status.body) == {"address": base, "registered": True}
    _, captured = await site(["--port", "0"], steps)
    assert captured.stdout.splitlines()[0].startswith("registered http://127.0.0.1:")
    assert captured.stdout.splitlines()[-1] == "deregistered from discovery"

@pytest.mark.external
async def test_the_port_from_the_command_line_is_used() -> None:
    free = free_port()
    seen: dict[str, Any] = {}

    async def steps(ctx: Context) -> None:
        seen["address"] = ctx.get(SERVER_ADDRESS)
    await site(["--port", str(free)], steps)
    assert seen["address"] == f"http://127.0.0.1:{free}"

@pytest.mark.external
async def test_discovery_deregisters_while_the_server_still_accepts() -> None:
    # Its registration requires the server's address, so it unloads first: by the order unloading follows.
    seen: dict[str, Any] = {}
    captured = Captured()

    async def steps(ctx: Context) -> None:
        seen["address"] = ctx.get(SERVER_ADDRESS)
    await site(["--port", "0"], steps, stdio=probing_stdio(seen, captured))
    assert seen["accepting"] is True

@pytest.mark.external
async def test_help_works_while_the_port_is_already_in_use() -> None:
    # Help ends the application before the command line is parsed, so the server, which needs the parsed options,
    # is never loaded.
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        status, captured = await site(["--help", "--port", str(port)])
    assert status == 0
    assert captured.stdout.startswith("usage: site")
    assert "--port PORT" in captured.stdout and "--routes" in captured.stdout

@pytest.mark.external
async def test_printing_routes_binds_nothing_and_registers_nothing() -> None:
    free = free_port()
    status, captured = await site(["--routes", "--port", str(free)])
    assert status == 0
    assert captured.stdout == "GET    /discovery\nPOST   /items\nGET    /items/{id}\n"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", free))  # still free: the server never bound it

def test_the_parser_activates_before_what_registers_options_with_it() -> None:
    order = [line.split(". ")[1].split()[0] for line in describe(compose([])).split("\n\n")[0].splitlines()[1:]]
    assert order.index("cli") < order.index("server") and order.index("cli") < order.index("routes-command")

def test_a_database_replaces_only_the_store_and_activates_before_it() -> None:
    description = describe(compose([], database=":memory:"))
    order = [line.split(". ")[1].split()[0] for line in description.split("\n\n")[0].splitlines()[1:]]
    assert order.index("sqlite") < order.index("sql") < order.index("items") < order.index("items-api")
    assert "items           sql items store" in description

@pytest.mark.external
async def test_items_kept_in_a_database_are_served_again_after_a_restart(tmp_path: Path) -> None:
    database = tmp_path / "site.sqlite"
    seen: list[tuple[int, Any]] = []

    async def add(ctx: Context) -> None:
        response = await ctx.get(HTTP).send(Request("POST", f"{ctx.get(SERVER_ADDRESS)}/items",
                                                    body=json.dumps({"name": "pen"}).encode()))
        seen.append((response.status, json.loads(response.body)))

    async def read(ctx: Context) -> None:
        response = await ctx.get(HTTP).send(Request("GET", f"{ctx.get(SERVER_ADDRESS)}/items/1"))
        seen.append((response.status, json.loads(response.body)))

    for steps in (add, read):
        status, _ = await site(["--port", "0"], steps, database=database)
        assert status == 0
    assert seen == [(201, {"id": 1, "name": "pen"}), (200, {"id": 1, "name": "pen"})]

def test_every_entry_does_a_job_of_its_own() -> None:
    # No entry exists only for wiring: the runner's application, then the composition, each with a component of its own.
    assert [entry.id for entry in compose([])] == [
        "stdio", "env", "cli", "routes", "server", "routes-command", "items", "items-api", "discovery",
    ]
