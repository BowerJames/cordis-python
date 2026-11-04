from typing import Any

import pytest

from cordis import Context
from cordis_loader import Entry, patched
from cordis_plugins import (
    HTTP,
    ROUTES,
    SERVER_ADDRESS,
    Captured,
    Request,
    asyncio_http_server,
    capturing_stdio,
    describe,
    health,
    httpx_http,
    route_table,
)
from cordis_plugins.testing import Steps, scenario
from echo.components import echo
from echo.entrypoints.echo_server import Server, compose, run


@pytest.fixture(params=["asyncio", "fastapi"])
def server(request: pytest.FixtureRequest) -> Server:
    chosen: Server = request.param
    return chosen

async def echo_server(steps: Steps, server: Server, *extra: Entry) -> tuple[int, Captured]:
    # The composition as shipped, on a free port, with captured stdio, an HTTP client, and a scenario that talks to it.
    capture, captured = capturing_stdio()
    entries = patched(compose(port=0, server=server), Entry("stdio", capture), Entry("http", httpx_http),
                      Entry("scenario", lambda: scenario(steps, requires=(HTTP, SERVER_ADDRESS))), *extra)
    return await run(entries), captured


@pytest.mark.external
async def test_health_answers_ok(server: Server) -> None:
    async def steps(ctx: Context) -> None:
        response = await ctx.get(HTTP).send(Request("GET", f"{ctx.get(SERVER_ADDRESS)}/health"))
        assert (response.status, response.text()) == (200, "ok")
    status, _ = await echo_server(steps, server)
    assert status == 0

@pytest.mark.external
async def test_echo_sends_the_body_back_unchanged_and_described_the_same(server: Server) -> None:
    async def steps(ctx: Context) -> None:
        body = bytes(range(256))
        response = await ctx.get(HTTP).send(Request("POST", f"{ctx.get(SERVER_ADDRESS)}/echo",
                                                    (("Content-Type", "image/png"),), body))
        assert (response.status, response.header("content-type"), response.body) == (200, "image/png", body)
    status, _ = await echo_server(steps, server)
    assert status == 0

@pytest.mark.external
async def test_echo_without_a_content_type_answers_as_bytes(server: Server) -> None:
    async def steps(ctx: Context) -> None:
        response = await ctx.get(HTTP).send(Request("POST", f"{ctx.get(SERVER_ADDRESS)}/echo", body=b"raw"))
        assert (response.header("content-type"), response.body) == ("application/octet-stream", b"raw")
    status, _ = await echo_server(steps, server)
    assert status == 0

@pytest.mark.external
async def test_only_the_declared_methods_are_answered(server: Server) -> None:
    async def steps(ctx: Context) -> None:
        base, http = ctx.get(SERVER_ADDRESS), ctx.get(HTTP)
        refused = await http.send(Request("GET", f"{base}/echo"))
        assert (refused.status, refused.header("allow")) == (405, "POST")
        assert (await http.send(Request("POST", f"{base}/health"))).status == 405
        assert (await http.send(Request("GET", f"{base}/nowhere"))).status == 404
    status, _ = await echo_server(steps, server)
    assert status == 0

@pytest.mark.external
async def test_the_server_is_announced_with_its_routes_once_started_and_its_end_is_announced(server: Server) -> None:
    seen: dict[str, Any] = {}

    async def steps(ctx: Context) -> None:
        seen["address"] = ctx.get(SERVER_ADDRESS)
    status, captured = await echo_server(steps, server)
    assert status == 0
    assert captured.stdout == f"listening on {seen['address']}\n  POST   /echo\n  GET    /health\nshutting down\n"

def test_the_composition_is_described_without_running_anything() -> None:
    assert describe(compose()) == """\
Activation order (unloaded in reverse):
  1. application
  2. stdio
  3. env
  4. routes
  5. server      after application (application), routes (routes), env (get_env)
  6. health      after routes (routes)
  7. echo        after routes (routes)
  8. announce    after application (application), server (server_address), routes (routes), stdio (stdout)

Entries:
  application  application
  stdio        stdio
  env          env os
  routes       route table
  server       asyncio http server
  health       health
  echo         echo
  announce     announce

Keys:
  application         provided by application; required by server, announce
  stdout              provided by stdio; required by announce
  stderr              provided by stdio
  stdout_is_terminal  provided by stdio
  stderr_is_terminal  provided by stdio
  get_env             provided by env; required by server
  routes              provided by routes; required by server, health, echo, announce
  server_address      provided by server; required by announce"""

def test_choosing_fastapi_replaces_only_the_server() -> None:
    plain, chosen = compose(), compose(server="fastapi")
    assert [entry.id for entry in plain] == [entry.id for entry in chosen]
    assert [entry for entry in plain if entry.id != "server"] == [entry for entry in chosen if entry.id != "server"]

@pytest.mark.external
async def test_two_servers_kept_apart_each_answer_only_their_own_routes() -> None:
    # A second route table and server in a group isolating their keys: realms keep the two apart, with no keys of
    # their own to wire, and the routes inside the group reach its table alone.
    seen: dict[str, Any] = {}

    async def steps(ctx: Context) -> None:
        main = ctx.get(SERVER_ADDRESS)
        group = next(fiber for fiber in ctx.runtime.fibers() if fiber.name == "admin")
        inner = next(fiber for fiber in ctx.runtime.children(group) if fiber.name == "asyncio http server")
        admin = inner.table[inner.realms[SERVER_ADDRESS]].value
        http = ctx.get(HTTP)
        seen["main health"] = (await http.send(Request("GET", f"{main}/health"))).status
        seen["admin health"] = (await http.send(Request("GET", f"{admin}/health"))).status
        seen["main echo"] = (await http.send(Request("POST", f"{main}/echo", body=b"x"))).status
        seen["admin echo"] = (await http.send(Request("POST", f"{admin}/echo", body=b"x"))).status

    admin = Entry("admin", children=[Entry("routes", route_table),
                                     Entry("server", asyncio_http_server, config={"port": 0}),
                                     Entry("health", health)],
                  isolate={ROUTES: True, SERVER_ADDRESS: True})
    capture, _ = capturing_stdio()
    entries = [*(entry for entry in compose(port=0) if entry.id in ("stdio", "env", "routes", "server")), Entry("echo", echo), Entry("http", httpx_http),
               Entry("scenario", lambda: scenario(steps, requires=(HTTP, SERVER_ADDRESS))), admin]
    status = await run(patched(entries, Entry("stdio", capture)))
    assert status == 0
    assert seen == {"main health": 404, "admin health": 200, "main echo": 200, "admin echo": 404}
