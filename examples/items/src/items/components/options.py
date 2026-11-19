from dataclasses import dataclass
from typing import Iterator

from cordis import Component, Context, Inverse
from cordis_plugins import APPLICATION, ARGS, CLI, ROUTES, STDOUT, HttpServerConfig, option, switch


def port_number(text: str) -> int:
    port = int(text)
    if not 0 <= port <= 65535:
        raise ValueError(text)
    return port

HOST = option("host", ("--host",), "the address to listen on", str)
PORT = option("port", ("--port",), "the port to listen on (0 lets the system choose)", port_number)
LIST_ROUTES = switch("routes", ("--routes",), "print the route table and exit")

@dataclass(frozen=True, slots=True)
class ServerCliConfig:
    # Which server to run: the composition's choice, as any component taking an HttpServerConfig.
    server: Component[HttpServerConfig]

def _server_cli(ctx: Context, config: ServerCliConfig) -> Iterator[Inverse]:
    def with_arguments(inner: Context) -> Inverse | None:
        args = inner.get(ARGS)
        if args(LIST_ROUTES):
            return None
        return inner.use(config.server, HttpServerConfig(host=args(HOST), port=args(PORT))).retire

    yield ctx.get(CLI).add(HOST)
    yield ctx.get(CLI).add(PORT)
    yield ctx.inject([ARGS], with_arguments, name="server").retire

# The server's command-line options, in one component. Once they are parsed it instantiates the server with them as its
# configuration: the server knows nothing of a command line, and it is the server's parent, so it leaves with it. A run
# that only lists the routes serves nothing, so it never binds a port.
server_cli = Component("server cli", _server_cli, config=ServerCliConfig, requires=(CLI,))

def _routes_command_listing(ctx: Context) -> None:
    if not ctx.get(ARGS)(LIST_ROUTES):
        return
    write = ctx.get(STDOUT)
    for route in sorted(ctx.get(ROUTES).routes(), key=lambda route: (route.path, route.method)):
        write(f"{route.method.upper():<6} {route.path}\n")
    ctx.get(APPLICATION).end(0)

def _routes_command(ctx: Context) -> Iterator[Inverse]:
    yield ctx.get(CLI).add(LIST_ROUTES)
    yield ctx.inject([APPLICATION, ARGS, ROUTES, STDOUT], _routes_command_listing, name="route _routes_command_listing").retire

# --routes prints the route table and ends the application. Routes live in their own table, so they are all there
# once everything has loaded, whether or not any server is.
routes_command = Component("routes command", _routes_command, requires=(CLI,))
