import argparse
import sys
from typing import Any, NoReturn, Sequence

from cordis import Component, Context, Entries, Inverse

from ..contracts import ARGS, CLI, STDERR, STDOUT, Argument, Arguments, UsageError


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

    def registered(self) -> tuple[Argument[Any], ...]:
        return tuple(self._registered)

def argument_table() -> Component:
    # The arguments, for components to register theirs with as they load; it knows nothing of how they are parsed.
    def apply(ctx: Context) -> None:
        arguments = _Arguments()
        ctx.provide(CLI, Arguments(add=arguments.add, registered=arguments.registered))
    return Component("argument table", apply=apply, provides=(CLI,))

def argparse_args(argv: Sequence[str] | None = None, prog: str | None = None) -> Component:
    # Parses the command line as it activates, against every argument registered by then, and provides ARGS, so
    # components reading values wait for it as they would for any key. Whoever runs the composition loads it once
    # everything else has settled, when every component that will register has. Asked for help, it prints it and
    # provides nothing: whatever waits for the arguments never starts, so a program that runs only for what reads them
    # ends by itself. Given a command line that does not match, it prints the usage and fails with the UsageError.
    def apply(ctx: Context) -> None:
        given = list(sys.argv[1:] if argv is None else argv)
        parser = _Parser(prog=prog, add_help=False)
        parser.add_argument("-h", "--help", action="store_true", help="show this help and exit")
        dests = {f"argument_{index}": argument for index, argument in enumerate(ctx.get(CLI).registered())}
        for dest, argument in dests.items():
            _add(parser, dest, argument)
        # Help is looked for first, so it is shown even when the rest of the command line is incomplete.
        if _asks_for_help(given):
            ctx.get(STDOUT)(parser.format_help())
            return
        try:
            parsed = vars(parser.parse_args(given))
        except UsageError as error:
            ctx.get(STDERR)(f"{parser.format_usage()}{parser.prog}: error: {error}\n")
            raise
        values: dict[Argument[Any], Any] = {argument: tuple(parsed[dest]) if argument.kind == "repeated" else parsed[dest]
                                            for dest, argument in dests.items()}

        def read[T](argument: Argument[T], /) -> T:
            if argument not in values:
                raise LookupError(f"{argument.name} was never registered on the command line")
            value: T = values[argument]
            return value

        ctx.provide(ARGS, read)

    return Component("argparse args", apply=apply, requires=(CLI, STDOUT, STDERR), provides=(ARGS,))
