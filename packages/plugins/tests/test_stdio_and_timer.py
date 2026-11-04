import asyncio
from typing import Callable

import pytest

from cordis import Component, Context, Runtime
from cordis_plugins import (
    GET_ENV,
    STDERR,
    STDOUT,
    STDOUT_IS_TERMINAL,
    TIMER,
    capturing_stdio,
    environment,
    get_env_os,
    stdio,
    timer,
)


async def test_stdio_writes_to_whatever_sys_stdout_is_when_written(runtime: Runtime, capsys: pytest.CaptureFixture[str]) -> None:
    runtime.root.use(stdio())
    await runtime.settled()
    write = runtime.root.find(STDOUT)
    assert write is not None
    write("no newline is added")
    assert capsys.readouterr().out == "no newline is added"

async def test_capturing_stdio_keeps_what_is_written_and_says_terminal_as_told(runtime: Runtime) -> None:
    capture, captured = capturing_stdio(terminal=True)
    runtime.root.use(capture())
    await runtime.settled()
    for key, text in ((STDOUT, "out"), (STDERR, "err")):
        write = runtime.root.find(key)
        assert write is not None
        write(text)
    is_terminal = runtime.root.find(STDOUT_IS_TERMINAL)
    assert (captured.stdout, captured.stderr, is_terminal is not None and is_terminal()) == ("out", "err", True)

async def test_the_environment_is_the_process_or_what_is_given(runtime: Runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORDIS_TEST_VARIABLE", "set")
    process = runtime.root.use(get_env_os())
    await runtime.settled()
    get_env = runtime.root.find(GET_ENV)
    assert get_env is not None and get_env("CORDIS_TEST_VARIABLE") == "set"
    await process.dispose()
    runtime.root.use(environment({"ONLY": "this"}))
    await runtime.settled()
    given = runtime.root.find(GET_ENV)
    assert given is not None and (given("ONLY"), given("CORDIS_TEST_VARIABLE")) == ("this", None)

# The timer contract, run against every component keeping it.
TIMERS = [timer]

@pytest.mark.parametrize("keeper", TIMERS, ids=lambda keeper: keeper.__name__)
async def test_a_timer_set_as_an_effect_never_fires_after_its_fiber_unloads(
    runtime: Runtime, keeper: Callable[[], Component],
) -> None:
    fired: list[str] = []

    def apply(ctx: Context) -> None:
        ctx.effect(lambda: ctx.get(TIMER).after(0.01, lambda: fired.append("late")))
        ctx.effect(lambda: ctx.get(TIMER).after(0, lambda: fired.append("soon")))

    runtime.root.use(keeper())
    fiber = runtime.root.use(Component("waiter", apply=apply, requires=(TIMER,)))
    await runtime.settled()
    await asyncio.sleep(0.001)
    await fiber.dispose()
    await asyncio.sleep(0.02)
    assert fired == ["soon"]

@pytest.mark.parametrize("keeper", TIMERS, ids=lambda keeper: keeper.__name__)
async def test_an_interval_repeats_until_cancelled(runtime: Runtime, keeper: Callable[[], Component]) -> None:
    ticks: list[int] = []
    runtime.root.use(keeper())
    await runtime.settled()
    timers = runtime.root.find(TIMER)
    assert timers is not None
    cancel = timers.every(0.001, lambda: ticks.append(len(ticks)))
    while len(ticks) < 3:
        await asyncio.sleep(0.001)
    cancel()
    count = len(ticks)
    await asyncio.sleep(0.01)
    assert len(ticks) == count
    assert timers.pending() == 0
