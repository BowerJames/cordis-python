from typing import Any, Callable

from cordis import Component, Context
from plugins import APPLICATION, ARGS, CLI, ROUTES, STDOUT, Argument, option, switch


def port_number(text: str) -> int:
    port = int(text)
    if not 0 <= port <= 65535:
        raise ValueError(text)
    return port

HOST = option("host", ("--host",), "the address to listen on", str)
PORT = option("port", ("--port",), "the port to listen on (0 lets the system choose)", port_number)
LIST_ROUTES = switch("routes", ("--routes",), "print the route table and exit")

def _arguments(ctx: Context, *arguments: Argument[Any]) -> None:
    for argument in arguments:
        _register(ctx, argument)

def _register(ctx: Context, argument: Argument[Any]) -> None:
    ctx.effect(lambda: ctx.get(CLI).add(argument), f"argument {argument.name}")

def server_cli(server: Callable[..., Component]) -> Component:
    # The server's command-line options, in one component. Once they are parsed it instantiates the server with them
    # bound into its configuration: the server knows nothing of a command line, and it is the server's parent, so it
    # leaves with it. Which server is the composition's choice, given as server. A run that only lists the routes
    # serves nothing, so it never binds a port.
    def with_arguments(ctx: Context) -> None:
        args = ctx.get(ARGS)
        if not args(LIST_ROUTES):
            ctx.use(server(host=args(HOST), port=args(PORT)))

    def apply(ctx: Context) -> None:
        _arguments(ctx, HOST, PORT)
        ctx.inject([ARGS], with_arguments, name="server")

    return Component("server cli", apply=apply, requires=(CLI,))

def routes_command() -> Component:
    # --routes prints the route table and ends the application. Routes live in their own table, so they are all there
    # once everything has loaded, whether or not any server is.
    def listing(ctx: Context) -> None:
        if not ctx.get(ARGS)(LIST_ROUTES):
            return
        write = ctx.get(STDOUT)
        for route in sorted(ctx.get(ROUTES).routes(), key=lambda route: (route.path, route.method)):
            write(f"{route.method.upper():<6} {route.path}\n")
        ctx.get(APPLICATION).end(0)

    def apply(ctx: Context) -> None:
        _arguments(ctx, LIST_ROUTES)
        ctx.inject([APPLICATION, ARGS, ROUTES, STDOUT], listing, name="route listing")

    return Component("routes command", apply=apply, requires=(CLI,))
