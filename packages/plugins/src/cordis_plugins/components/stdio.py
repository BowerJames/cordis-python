import os
import sys
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, TextIO

from cordis import Component, Context, Inverse

from ..contracts import GET_ENV, STDERR, STDERR_IS_TERMINAL, STDOUT, STDOUT_IS_TERMINAL, Write

_STREAMS = (STDOUT, STDERR, STDOUT_IS_TERMINAL, STDERR_IS_TERMINAL)

def _writing_to(stream: Callable[[], TextIO]) -> Write:
    # The stream is looked up on every write, so redirecting sys.stdout or sys.stderr is honoured.
    def write(text: str) -> None:
        current = stream()
        current.write(text)
        current.flush()
    return write

def stdio() -> Component:
    def apply(ctx: Context) -> Iterator[Inverse]:
        def flush() -> None:
            sys.stdout.flush()
            sys.stderr.flush()

        # Flushed last, once nothing provided here can be written to any more.
        yield flush
        yield ctx.provide(STDOUT, _writing_to(lambda: sys.stdout))
        yield ctx.provide(STDERR, _writing_to(lambda: sys.stderr))
        yield ctx.provide(STDOUT_IS_TERMINAL, lambda: sys.stdout.isatty())
        yield ctx.provide(STDERR_IS_TERMINAL, lambda: sys.stderr.isatty())
    return Component("stdio", apply=apply, provides=_STREAMS)

@dataclass(slots=True)
class Captured:
    # Everything written to each stream while the component was loaded.
    stdout: str = ""
    stderr: str = ""

def capturing_stdio(terminal: bool = False) -> tuple[Callable[[], Component], Captured]:
    # A stand-in for stdio that keeps what is written instead of writing it, and says its streams are terminals or
    # not, as told, so both cases can be tested. Given as a factory, so it can stand in an entry of its own.
    captured = Captured()

    def to_stdout(text: str) -> None:
        captured.stdout += text

    def to_stderr(text: str) -> None:
        captured.stderr += text

    def apply(ctx: Context) -> Iterator[Inverse]:
        yield ctx.provide(STDOUT, to_stdout)
        yield ctx.provide(STDERR, to_stderr)
        yield ctx.provide(STDOUT_IS_TERMINAL, lambda: terminal)
        yield ctx.provide(STDERR_IS_TERMINAL, lambda: terminal)

    return lambda: Component("capturing stdio", apply=apply, provides=_STREAMS), captured

def get_env_os() -> Component:
    def apply(ctx: Context) -> Inverse:
        return ctx.provide(GET_ENV, os.getenv)
    return Component("env os", apply=apply, provides=(GET_ENV,))

def environment(variables: Mapping[str, str]) -> Component:
    # The environment as given, in place of the process's: what a test or a composition decides, and nothing else.
    fixed = dict(variables)

    def get_env(name: str) -> str | None:
        return fixed.get(name)

    def apply(ctx: Context) -> Inverse:
        return ctx.provide(GET_ENV, get_env)
    return Component("environment", apply=apply, provides=(GET_ENV,))
