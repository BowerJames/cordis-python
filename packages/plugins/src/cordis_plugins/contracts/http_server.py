import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping

from cordis import Inverse, Key

from .http import Headers

# Routes live in a table of their own, not in a server: contributors add to it and servers answer from it, and
# neither requires the other. Swapping one server for another leaves every route where it is, and a route added or
# withdrawn while a server runs is answered, or not, from the next request on.
#
# Every server provider keeps these promises:
#   a route in the table is answered by its handler; an unknown path is 404; a known path with another method is 405,
#   with an Allow header; a request it cannot read is 4xx; a body over max_body is 413; a handler that raises is 500
#   for that request alone, and is reported on STDERR when there is one.
#   The socket is bound while the server activates, and its address provided as SERVER_ADDRESS then; it serves from
#   then on, holding the runtime while it does. Unloaded, it stops accepting, lets requests in progress finish, and
#   returns.
#   It may add headers to a response, but never changes the status or body a handler returned.
# Websockets are optional: a provider that serves them accepts a connection on a socket route in the table and hands it
# to that route's handler, closing it once the handler returns; a handler that raises closes its connection with 1011
# and is reported. One that does not serve them answers socket routes with nothing.

@dataclass(slots=True, frozen=True)
class ServerRequest:
    method: str
    path: str
    query: Headers = ()
    headers: Headers = ()
    body: bytes = b""
    # The {name} parts of the route that matched.
    params: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        return next((value for key, value in self.headers if key.lower() == wanted), None)

@dataclass(slots=True, frozen=True)
class ServerResponse:
    status: int
    headers: Headers = ()
    body: bytes = b""

    @staticmethod
    def text(body: str, status: int = 200) -> "ServerResponse":
        return ServerResponse(status=status, headers=(("Content-Type", "text/plain; charset=utf-8"),), body=body.encode())

    @staticmethod
    def json(value: object, status: int = 200) -> "ServerResponse":
        return ServerResponse(status=status, headers=(("Content-Type", "application/json"),), body=json.dumps(value).encode())

type Handler = Callable[[ServerRequest], Awaitable[ServerResponse]]

@dataclass(slots=True, frozen=True, eq=False)
class Route:
    method: str
    # Exact segments, or {name} for one segment captured into params: "/greet/{name}".
    path: str
    handle: Handler

    def __str__(self) -> str:
        return f"{self.method.upper()} {self.path}"

@dataclass(slots=True, frozen=True)
class Socket:
    # One accepted websocket connection, as a socket route's handler sees it.
    path: str
    query: Headers
    headers: Headers
    # The next text message, or None once the client has gone.
    receive: Callable[[], Awaitable[str | None]]
    send: Callable[[str], Awaitable[None]]
    # Closes with a code and reason; closing a connection already closed does nothing.
    close: Callable[[int, str], Awaitable[None]]

type SocketHandler = Callable[[Socket], Awaitable[None]]

@dataclass(slots=True, frozen=True, eq=False)
class SocketRoute:
    # An exact path; the connection lasts as long as handle does.
    path: str
    handle: SocketHandler

    def __str__(self) -> str:
        return f"WS {self.path}"

@dataclass(slots=True, frozen=True)
class Matched:
    route: Route
    params: Mapping[str, str]

@dataclass(slots=True, frozen=True)
class NotAllowed:
    allowed: tuple[str, ...]

@dataclass(slots=True, frozen=True)
class RouteTable:
    # The value bound at ROUTES. Adding a route is an entry of its own, withdrawn by the inverse add returns, so
    # contributors come and go independently; two routes answering the same method and path are refused, as is a
    # path not starting with /, by add raising ValueError, which fails the contributor that tried.
    # Withdrawing a route takes it out of the table at once, so no new request reaches it, and then waits for the
    # calls of its handler still in progress, for as long as the runtime's grace: a contributor unloaded while a
    # request is in flight, at any time and not only as the program ends, still answers it, with everything it
    # required still there. A handler that waits for its own contributor to leave waits out that grace.
    add: Callable[[Route], Inverse]
    add_socket: Callable[[SocketRoute], Inverse]
    # Every route in the table now, in the order they were added.
    routes: Callable[[], tuple[Route, ...]]
    sockets: Callable[[], tuple[SocketRoute, ...]]
    # The socket route answering an exact path, if any.
    socket: Callable[[str], SocketRoute | None]
    # None when no route has this path; NotAllowed when some do, but not for this method.
    match: Callable[[str, str], Matched | NotAllowed | None]

ROUTES: Key[RouteTable] = Key("routes")
# Where the server listens, as a base URL such as http://127.0.0.1:8000.
SERVER_ADDRESS: Key[str] = Key("server_address")
