import os
import sys
from dataclasses import dataclass, field
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

def _stdio(ctx: Context) -> Iterator[Inverse]:
    def flush() -> None:
        sys.stdout.flush()
        sys.stderr.flush()

    # Flushed last, once nothing provided here can be written to any more.
    yield flush
    yield ctx.provide(STDOUT, _writing_to(lambda: sys.stdout))
    yield ctx.provide(STDERR, _writing_to(lambda: sys.stderr))
    yield ctx.provide(STDOUT_IS_TERMINAL, lambda: sys.stdout.isatty())
    yield ctx.provide(STDERR_IS_TERMINAL, lambda: sys.stderr.isatty())

stdio = Component("stdio", _stdio, provides=_STREAMS)

@dataclass(slots=True)
class Captured:
    # Everything written to each stream while the component was loaded.
    stdout: str = ""
    stderr: str = ""

def capturing_stdio(terminal: bool = False) -> tuple[Component[None], Captured]:
    # A stand-in for stdio that keeps what is written instead of writing it, and says its streams are terminals or
    # not, as told, so both cases can be tested. Each call makes a component of its own, bound to what it captures.
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

    return Component("capturing stdio", apply, provides=_STREAMS), captured

def _get_env_os(ctx: Context) -> Inverse:
    return ctx.provide(GET_ENV, os.getenv)

get_env_os = Component("env os", _get_env_os, provides=(GET_ENV,))

@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    variables: Mapping[str, str] = field(default_factory=dict)

def _environment(ctx: Context, config: EnvironmentConfig) -> Inverse:
    fixed = dict(config.variables)

    def get_env(name: str) -> str | None:
        return fixed.get(name)

    return ctx.provide(GET_ENV, get_env)

# The environment as configured, in place of the process's: what a test or a composition decides, and nothing else.
# It accepts no new configuration in place: whoever read a variable read it once, so only a revision, which reactivates
# every reader, ends where loading the new variables from scratch would.
environment = Component("environment", _environment, config=EnvironmentConfig, provides=(GET_ENV,))
