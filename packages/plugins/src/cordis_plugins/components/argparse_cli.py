import argparse
import sys
from typing import Any, NoReturn, Sequence

from cordis import Component, Context, Entries, Inverse

from ..contracts import APPLICATION, ARGS, CLI, STDERR, STDOUT, Argument, Arguments, UsageError


class _Parser(argparse.ArgumentParser):
    # Reports problems by raising, so that parsing never exits the process.
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise UsageError(message or "")

def _add(parser: argparse.ArgumentParser, dest: str, argument: Argument[Any]) -> None:
    # Every argument gets a destination of its own, so arguments from different components never share a value.
    match argument.kind:
        case "positional":
            parser.add_argument(dest, metavar=argument.name, help=argument.help, type=argument.parse)
        case "option":
            parser.add_argument(*argument.flags, dest=dest, metavar=argument.name.upper(), help=argument.help,
                                type=argument.parse, default=argument.default)
        case "repeated":
            parser.add_argument(*argument.flags, dest=dest, metavar=argument.name.upper(), help=argument.help,
                                type=argument.parse, action="append", default=[])
        case "switch":
            parser.add_argument(*argument.flags, dest=dest, help=argument.help, action="store_true")

def _asks_for_help(given: Sequence[str]) -> bool:
    # Anything after "--" is a value, never a request for help.
    options = given[:given.index("--")] if "--" in given else given
    return "-h" in options or "--help" in options

class _Arguments:
    __slots__ = ("_registered",)

    def __init__(self) -> None:
        self._registered: Entries[Argument[Any]] = Entries()

    def add(self, argument: Argument[Any], /) -> Inverse:
        return self._registered.add(argument)

    def __iter__(self) -> Any:
        return iter(self._registered)

def argparse_cli(argv: Sequence[str] | None = None, prog: str | None = None) -> Component:
    # Provides CLI as soon as it loads, for components to register their arguments with, and parses the command line
    # once the application has started, when every component that will register has. Only then does it provide ARGS,
    # so components reading values wait for it as they would for any key. Asked for help, it prints it and ends the
    # application; given a command line that does not match, it prints the usage and ends it with status 2.
    def apply(ctx: Context) -> None:
        arguments = _Arguments()
        ctx.provide(CLI, Arguments(add=arguments.add))
        app = ctx.get(APPLICATION)
        given = list(sys.argv[1:] if argv is None else argv)

        def parse() -> None:
            registered = list(arguments)
            parser = _Parser(prog=prog, add_help=False)
            parser.add_argument("-h", "--help", action="store_true", help="show this help and exit")
            dests = {f"argument_{index}": argument for index, argument in enumerate(registered)}
            for dest, argument in dests.items():
                _add(parser, dest, argument)
            # Help is looked for first, so it is shown even when the rest of the command line is incomplete.
            if _asks_for_help(given):
                ctx.get(STDOUT)(parser.format_help())
                app.end(0)
                return
            try:
                parsed = vars(parser.parse_args(given))
            except UsageError as error:
                ctx.get(STDERR)(f"{parser.format_usage()}{parser.prog}: error: {error}\n")
                app.end(2)
                app.fail(error, "argparse cli", ctx.fiber)
                return
            values: dict[Argument[Any], Any] = {argument: tuple(parsed[dest]) if argument.kind == "repeated" else parsed[dest]
                      for dest, argument in dests.items()}

            def read[T](argument: Argument[T], /) -> T:
                if argument not in values:
                    raise LookupError(f"{argument.name} was never registered on the command line")
                value: T = values[argument]
                return value

            ctx.provide(ARGS, read)

        ctx.effect(lambda: app.when_started(parse), "parse once started")

    return Component("argparse cli", apply=apply, requires=(APPLICATION, STDOUT, STDERR), provides=(CLI, ARGS))
