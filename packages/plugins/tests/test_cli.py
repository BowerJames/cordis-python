from typing import Any

from cordis import Component, Context
from cordis_loader import Entry
from cordis_plugins import (
    ARGS,
    CLI,
    UsageError,
    argparse_args,
    argument_table,
    capturing_stdio,
    option,
    positional,
    repeated,
    switch,
)
from cordis_plugins.testing import run

NAME = positional("name", "who to greet", str)
TIMES = option("times", ("-n", "--times"), "how many times", int, default=1)
TAGS = repeated("tag", ("-t", "--tag"), "a tag", str)
LOUD = switch("loud", ("--loud",), "shout")

def greeter(seen: list[Any]) -> Component:
    # Registers its arguments as it loads, and reads them in a child that waits for them to be parsed.
    def read(ctx: Context) -> None:
        args = ctx.get(ARGS)
        seen.append((args(NAME), args(TIMES), args(TAGS), args(LOUD)))

    def apply(ctx: Context) -> None:
        for argument in (NAME, TIMES, TAGS, LOUD):
            add(ctx, argument)
        ctx.inject([ARGS], read)

    return Component("greeter", apply=apply, requires=(CLI,))

def add(ctx: Context, argument: Any) -> None:
    ctx.effect(lambda: ctx.get(CLI).add(argument))

async def parse(argv: list[str], seen: list[Any]) -> tuple[int, str, str, bool]:
    # As an entrypoint with a command line runs: the parser is loaded once every argument has been registered.
    capture, captured = capturing_stdio()
    outcome = await run([
        Entry("stdio", capture),
        Entry("cli", argument_table),
        Entry("greeter", greeter, config={"seen": seen}),
    ], then=[Entry("args", argparse_args, config={"argv": argv, "prog": "greet"})])
    return outcome.status, captured.stdout, captured.stderr, bool(outcome.failed_with(UsageError))

async def test_arguments_registered_by_components_are_parsed_once_they_have_all_loaded() -> None:
    seen: list[Any] = []
    status, _, _, _ = await parse(["ada", "-n", "3", "-t", "a", "--tag", "b", "--loud"], seen)
    assert status == 0
    assert seen == [("ada", 3, ("a", "b"), True)]

async def test_defaults_apply_to_what_is_not_given() -> None:
    seen: list[Any] = []
    await parse(["ada"], seen)
    assert seen == [("ada", 1, (), False)]

async def test_help_lists_every_registered_argument_and_ends_without_reading_any() -> None:
    seen: list[Any] = []
    status, out, _, usage = await parse(["--help"], seen)
    assert (status, usage, seen) == (0, False, [])
    assert "usage: greet [-h] [-n TIMES] [-t TAG] [--loud] name" in out

async def test_a_command_line_that_does_not_match_prints_the_usage_and_fails_with_a_usage_error() -> None:
    # What status a usage error exits with is the entrypoint's to say; the run only reports that it failed.
    seen: list[Any] = []
    status, _, err, usage = await parse(["-n", "many"], seen)
    assert (status, usage, seen) == (1, True, [])
    assert err.startswith("usage: greet")
    assert "argument -n/--times: invalid int value: 'many'" in err

async def test_a_component_that_is_not_loaded_has_no_arguments() -> None:
    capture, captured = capturing_stdio()
    await run([Entry("stdio", capture), Entry("cli", argument_table),
               Entry("greeter", greeter, config={"seen": []}, disabled=True)],
              then=[Entry("args", argparse_args, config={"argv": ["--help"], "prog": "x"})])
    assert "name" not in captured.stdout
