import asyncio
import contextlib
import socket
from contextlib import suppress
from http import HTTPStatus
from typing import AsyncIterator, Generator
from urllib.parse import parse_qsl, unquote, urlsplit

import uvicorn
from fastapi import FastAPI
from starlette.requests import ClientDisconnect
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from cordis import Component, Context, Entries, Inverse

from ..contracts import (
    APPLICATION,
    GET_ENV,
    ROUTES,
    SERVER_ADDRESS,
    STDERR,
    GetEnv,
    Matched,
    NotAllowed,
    Route,
    RouteTable,
    ServerRequest,
    ServerResponse,
    Socket,
    SocketRoute,
    serve,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_MAX_BODY = 1_048_576
HTTP_HOST_VARIABLE = "HTTP_HOST"
HTTP_PORT_VARIABLE = "HTTP_PORT"
MAX_HEADER_BYTES = 65_536

class _Routes:
    # Every route and socket route an entry of its own.
    __slots__ = ("_routes", "_sockets")

    def __init__(self) -> None:
        self._routes: Entries[Route] = Entries()
        self._sockets: Entries[SocketRoute] = Entries()

    def add(self, route: Route) -> Inverse:
        if not route.path.startswith("/"):
            raise ValueError(f"route {route} must start with /")
        if any((other.method.upper(), other.path) == (route.method.upper(), route.path) for other in self._routes):
            raise ValueError(f"two routes answer {route}")
        return self._routes.add(route)

    def add_socket(self, route: SocketRoute) -> Inverse:
        if not route.path.startswith("/"):
            raise ValueError(f"socket route {route.path} must start with /")
        if any(other.path == route.path for other in self._sockets):
            raise ValueError(f"two socket routes answer {route.path}")
        return self._sockets.add(route)

    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes)

    def sockets(self) -> tuple[SocketRoute, ...]:
        return tuple(self._sockets)

    def socket(self, path: str) -> SocketRoute | None:
        return next((route for route in self._sockets if route.path == path), None)

    def match(self, method: str, path: str) -> Matched | NotAllowed | None:
        # None when no route has this path; NotAllowed when some do, but not for this method.
        wanted = path.split("/")
        allowed: list[str] = []
        for route in self._routes:
            params = _params(route.path.split("/"), wanted)
            if params is None:
                continue
            if route.method.upper() == method.upper():
                return Matched(route=route, params=params)
            allowed.append(route.method.upper())
        return NotAllowed(allowed=tuple(sorted(set(allowed)))) if allowed else None

def _params(pattern: list[str], wanted: list[str]) -> dict[str, str] | None:
    if len(pattern) != len(wanted):
        return None
    params: dict[str, str] = {}
    for expected, given in zip(pattern, wanted):
        if expected.startswith("{") and expected.endswith("}"):
            if not given:
                return None
            params[expected[1:-1]] = given
        elif expected != given:
            return None
    return params

def route_table() -> Component:
    # The routes, for contributors to add to and servers to answer from; neither requires the other.
    def apply(ctx: Context) -> None:
        table = _Routes()
        ctx.provide(ROUTES, RouteTable(add=table.add, add_socket=table.add_socket, routes=table.routes,
                                       sockets=table.sockets, socket=table.socket, match=table.match))
    return Component("route table", apply=apply, provides=(ROUTES,))

def listen_on(host: str | None, port: int | None, get_env: GetEnv) -> tuple[str, int]:
    # The factory's arguments first, then the environment, then the defaults.
    chosen_host = host or get_env(HTTP_HOST_VARIABLE) or DEFAULT_HOST
    if port is not None:
        return chosen_host, port
    text = get_env(HTTP_PORT_VARIABLE)
    if text is None:
        return chosen_host, DEFAULT_PORT
    try:
        chosen_port = int(text)
    except ValueError:
        raise ValueError(f"{HTTP_PORT_VARIABLE} must be a port number, not {text!r}") from None
    if not 0 <= chosen_port <= 65535:
        raise ValueError(f"{HTTP_PORT_VARIABLE} must be between 0 and 65535, not {text!r}")
    return chosen_host, chosen_port

def address_of(host: str, port: int) -> str:
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"

def _listening(host: str, port: int) -> socket.socket:
    # A socket bound to host and port and listening, so connections queue until whoever owns it accepts them.
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listening = socket.socket(family, socket.SOCK_STREAM)
    try:
        listening.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listening.bind((host, port))
        listening.listen()
        listening.setblocking(False)
    except BaseException:
        listening.close()
        raise
    return listening

def _plain(status: HTTPStatus, headers: tuple[tuple[str, str], ...] = ()) -> ServerResponse:
    return ServerResponse(status=status.value, headers=(("Content-Type", "text/plain; charset=utf-8"), *headers),
                          body=f"{status.value} {status.phrase}\n".encode())

async def answer(table: RouteTable, request: ServerRequest, ctx: Context) -> ServerResponse:
    # Matched against the table as it stands now, so a route added or withdrawn is answered from the next request.
    found = table.match(request.method, request.path)
    if found is None:
        return _plain(HTTPStatus.NOT_FOUND)
    if isinstance(found, NotAllowed):
        return _plain(HTTPStatus.METHOD_NOT_ALLOWED, (("Allow", ", ".join(found.allowed)),))
    try:
        return await found.route.handle(ServerRequest(method=request.method, path=request.path, query=request.query,
                                                      headers=request.headers, body=request.body, params=found.params))
    except Exception as error:  # noqa: BLE001 - a failing handler fails its own request, never the server
        report = ctx.find(STDERR)
        if report is not None:
            report(f"{found.route} failed: {error!r}\n")
        return _plain(HTTPStatus.INTERNAL_SERVER_ERROR)

# --- asyncio ------------------------------------------------------------------------------------------------------

# A small HTTP/1.1 server on asyncio streams, for showing the framework at work rather than for production: one
# request per connection (no keep-alive), Content-Length bodies only (no chunked requests), no TLS, no websockets.

def _encode(response: ServerResponse) -> bytes:
    try:
        reason = HTTPStatus(response.status).phrase
    except ValueError:
        reason = ""
    given = {name.lower() for name, _ in response.headers}
    lines = [f"HTTP/1.1 {response.status} {reason}".rstrip()]
    lines.extend(f"{name}: {value}" for name, value in response.headers if name.lower() not in ("content-length", "connection"))
    if "content-type" not in given and response.body:
        lines.append("Content-Type: application/octet-stream")
    lines.append(f"Content-Length: {len(response.body)}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + response.body

async def _read(reader: asyncio.StreamReader, max_body: int) -> ServerRequest | ServerResponse:
    # The request, or the response that answers a request that cannot be served.
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        return _plain(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
    request_line, *header_lines = head.decode("latin-1").split("\r\n")
    parts = request_line.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return _plain(HTTPStatus.BAD_REQUEST)
    method, target, _ = parts
    headers: list[tuple[str, str]] = []
    for line in header_lines:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator or not name.strip():
            return _plain(HTTPStatus.BAD_REQUEST)
        headers.append((name.strip(), value.strip()))
    if any(name.lower() == "transfer-encoding" for name, _ in headers):
        return _plain(HTTPStatus.NOT_IMPLEMENTED)
    length_text = next((value for name, value in headers if name.lower() == "content-length"), "0")
    if not length_text.isdigit():
        return _plain(HTTPStatus.BAD_REQUEST)
    length = int(length_text)
    if length > max_body:
        return _plain(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    body = await reader.readexactly(length) if length else b""
    split = urlsplit(target)
    return ServerRequest(method=method.upper(), path=unquote(split.path) or "/",
                         query=tuple(parse_qsl(split.query, keep_blank_values=True)), headers=tuple(headers), body=body)

def asyncio_http_server(host: str | None = None, port: int | None = None, max_body: int = DEFAULT_MAX_BODY) -> Component:
    async def apply(ctx: Context) -> AsyncIterator[Inverse]:
        table = ctx.get(ROUTES)
        chosen_host, chosen_port = listen_on(host, port, ctx.get(GET_ENV))
        in_progress: set[asyncio.Task[None]] = set()

        async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            assert task is not None
            in_progress.add(task)
            try:
                read = await _read(reader, max_body)
                response = read if isinstance(read, ServerResponse) else await answer(table, read, ctx)
                writer.write(_encode(response))
                await writer.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass  # the client went away; there is nobody left to answer
            finally:
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()
                in_progress.discard(task)

        # Bound and listening now, so the address is known as the server activates, and early connections wait in the
        # operating system's queue until the server starts accepting them, once the application has started.
        listening = _listening(chosen_host, chosen_port)
        server = await asyncio.start_server(connection, sock=listening, start_serving=False, limit=MAX_HEADER_BYTES)

        async def close() -> None:
            server.close()
            for task in in_progress:
                task.cancel()
            await server.wait_closed()

        yield close
        bound_host, bound_port = server.sockets[0].getsockname()[:2]
        ctx.provide(SERVER_ADDRESS, address_of(bound_host, bound_port))

        async def serving(stopping: asyncio.Event) -> None:
            await server.start_serving()
            await stopping.wait()
            # Told to stop: stop accepting, then let requests in progress finish (the application's grace bounds this).
            server.close()
            while in_progress:
                await asyncio.wait(set(in_progress))

        serve(ctx, "http server", serving)

    return Component("asyncio http server", apply=apply, requires=(APPLICATION, ROUTES, GET_ENV),
                     provides=(SERVER_ADDRESS,))

# --- FastAPI ------------------------------------------------------------------------------------------------------

# The same contract, served by FastAPI on uvicorn, with websockets. FastAPI's routing and validation are not used: one
# catch-all route answers every path from the table, so the table stays the one authority on what is served.

_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

class _Server(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.stopping = asyncio.Event()

    # Signals belong to the application: uvicorn neither installs handlers nor re-raises signals after shutting down.
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield

    async def main_loop(self) -> None:
        # As uvicorn's own, ticking every tenth of a second, but woken the moment it is told to stop.
        counter = 0
        while not await self.on_tick(counter):
            counter = (counter + 1) % 864_000
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), 0.1)

def _starlette(response: ServerResponse) -> StarletteResponse:
    converted = StarletteResponse(content=response.body, status_code=response.status)
    del converted.headers["content-length"]
    for name, value in response.headers:
        converted.headers.append(name, value)
    converted.headers["content-length"] = str(len(response.body))
    return converted

async def _body(request: StarletteRequest, max_body: int) -> bytes | None:
    # The body, or None when it is larger than max_body; checked as it arrives, so an oversized body is never kept.
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > max_body:
        return None
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > max_body:
            return None
    return bytes(received)

def _app(table: RouteTable, max_body: int, ctx: Context) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    async def endpoint(request: StarletteRequest) -> StarletteResponse:
        try:
            body = await _body(request, max_body)
        except ClientDisconnect:
            return _starlette(_plain(HTTPStatus.BAD_REQUEST))
        if body is None:
            return _starlette(_plain(HTTPStatus.REQUEST_ENTITY_TOO_LARGE))
        served = ServerRequest(method=request.method.upper(), path=request.url.path,
                               query=tuple(request.query_params.multi_items()), headers=tuple(request.headers.items()),
                               body=body)
        return _starlette(await answer(table, served, ctx))

    async def socket_endpoint(websocket: WebSocket) -> None:
        route = table.socket(websocket.url.path)
        if route is None:
            await websocket.close(4404, "no socket route answers this path")
            return
        await websocket.accept()

        async def receive() -> str | None:
            try:
                return await websocket.receive_text()
            except WebSocketDisconnect:
                return None

        async def close(code: int, reason: str) -> None:
            if websocket.application_state is not WebSocketState.DISCONNECTED and \
                    websocket.client_state is not WebSocketState.DISCONNECTED:
                with suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.close(code, reason)

        connected = Socket(path=websocket.url.path, query=tuple(websocket.query_params.multi_items()),
                           headers=tuple(websocket.headers.items()), receive=receive, send=websocket.send_text,
                           close=close)
        try:
            await route.handle(connected)
        except Exception as error:  # noqa: BLE001 - a failing handler fails its own connection, never the server
            report = ctx.find(STDERR)
            if report is not None:
                report(f"{route} failed: {error!r}\n")
            await close(1011, "internal error")
            return
        await close(1000, "")

    app.add_api_route("/{path:path}", endpoint, methods=_METHODS, include_in_schema=False)
    app.add_api_websocket_route("/{path:path}", socket_endpoint)
    return app

def fastapi_http_server(host: str | None = None, port: int | None = None, max_body: int = DEFAULT_MAX_BODY) -> Component:
    def apply(ctx: Context) -> None:
        chosen_host, chosen_port = listen_on(host, port, ctx.get(GET_ENV))
        # Bound and listening now, so the address is known as the server activates, and early connections wait in the
        # operating system's queue until uvicorn starts accepting them.
        listening = _listening(chosen_host, chosen_port)
        # Closed after serving has stopped: inverses run newest first, and serving is started after this.
        ctx.effect(lambda: listening.close, "close the listening socket")
        bound_host, bound_port = listening.getsockname()[:2]
        ctx.provide(SERVER_ADDRESS, address_of(bound_host, bound_port))
        config = uvicorn.Config(_app(ctx.get(ROUTES), max_body, ctx), lifespan="off", log_config=None,
                                log_level="critical", access_log=False, timeout_graceful_shutdown=None)
        server = _Server(config)

        async def serving(stopping: asyncio.Event) -> None:
            running = asyncio.create_task(server.serve(sockets=[listening]))
            ending = asyncio.create_task(stopping.wait())
            try:
                await asyncio.wait({running, ending}, return_when=asyncio.FIRST_COMPLETED)
                if ending.done():
                    # Told to stop: stop accepting at once, wake uvicorn rather than waiting for its next tick, then
                    # let it finish the requests in progress.
                    for listener in getattr(server, "servers", []):
                        listener.close()
                    server.should_exit = True
                    server.stopping.set()
                await running
            finally:
                ending.cancel()
                if not running.done():
                    server.force_exit = True
                    running.cancel()
                    with suppress(asyncio.CancelledError):
                        await running

        serve(ctx, "http server", serving)

    return Component("fastapi http server", apply=apply, requires=(APPLICATION, ROUTES, GET_ENV),
                     provides=(SERVER_ADDRESS,))
