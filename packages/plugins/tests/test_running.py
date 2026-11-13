# What an entrypoint runs a composition with: the failure handler and the signals, and the testing helpers that are
# entrypoints in small. The lifecycle itself, holding and being done, is the kernel's, and tested there.
import asyncio
import os
import signal

import pytest

from cordis import Component, Context, open_runtime
from cordis_loader import Entry, Loader, describe
from cordis_plugins import EXIT_STATUS, StopOnFailure, stop_on_signals
from cordis_plugins.testing import run, run_scenario


def worker(log: list[str], name: str, work_for: float | None = None, fail: bool = False) -> Component:
    def apply(ctx: Context) -> None:
        async def work(stopping: asyncio.Event) -> None:
            log.append(f"{name} starts")
            if fail:
                raise RuntimeError(f"{name} broke")
            try:
                await asyncio.wait_for(stopping.wait(), work_for)
                log.append(f"{name} told to stop")
            except TimeoutError:
                log.append(f"{name} done")
        ctx.spawn(work, name)
    return Component(name, apply=apply)

async def test_a_run_ends_by_itself_once_nothing_holds_it() -> None:
    log: list[str] = []
    outcome = await run([Entry("a", worker, config={"log": log, "name": "a", "work_for": 0.01}),
                         Entry("b", worker, config={"log": log, "name": "b", "work_for": 0.02})])
    assert (outcome.status, outcome.failures) == (0, ())
    assert log == ["a starts", "b starts", "a done", "b done"]

async def test_nothing_to_run_ends_at_once() -> None:
    assert (await run([])).status == 0

async def test_work_that_fails_stops_the_run_with_status_1_and_the_rest_is_told_to_stop() -> None:
    log: list[str] = []
    outcome = await run([Entry("steady", worker, config={"log": log, "name": "steady"}),
                         Entry("broken", worker, config={"log": log, "name": "broken", "fail": True})])
    assert outcome.status == 1
    [(where, error)] = outcome.failures
    assert (where, str(error), error.__notes__) == ("broken", "broken broke", ["in broken"])
    assert "steady told to stop" in log
    assert [str(error) for error in outcome.failed_with(RuntimeError)] == ["broken broke"]

async def test_a_component_that_fails_to_activate_stops_the_run() -> None:
    def broken(ctx: Context) -> None:
        raise ValueError("misconfigured")
    outcome = await run([Entry("broken", lambda: Component("broken", apply=broken))])
    assert outcome.status == 1
    assert [str(error) for _, error in outcome.failures] == ["misconfigured"]

async def test_a_contained_failure_goes_to_its_handler_and_the_run_goes_on() -> None:
    log: list[str] = []
    contained: list[str] = []

    def host(ctx: Context) -> None:
        def broken(inner: Context) -> None:
            raise RuntimeError("only this part fails")
        child = ctx.use(Component("part", apply=broken))
        ctx.contain(lambda fiber, error: contained.append(f"{error} in {fiber.name}"), child)

    outcome = await run([Entry("host", lambda: Component("host", apply=host)),
                         Entry("a", worker, config={"log": log, "name": "a", "work_for": 0.01})])
    assert (outcome.status, outcome.failures) == (0, ())
    assert contained == ["only this part fails in part"]
    assert log == ["a starts", "a done"]

async def test_a_component_decides_the_status_by_providing_it() -> None:
    def searching(ctx: Context) -> None:
        async def search(stopping: asyncio.Event) -> None:
            await asyncio.sleep(0)
            ctx.provide(EXIT_STATUS, 3)  # found nothing, say
        ctx.spawn(search, "search")

    outcome = await run([Entry("search", lambda: Component("search", apply=searching, provides=(EXIT_STATUS,)))])
    assert (outcome.status, outcome.failures) == (3, ())

async def test_a_failure_is_never_status_0() -> None:
    failed = StopOnFailure()
    assert (failed.status(), failed.status(0), failed.status(4)) == (0, 0, 4)
    async with open_runtime(on_error=failed) as runtime:
        def broken(ctx: Context) -> None:
            raise RuntimeError("broken")
        runtime.root.use(Component("broken", apply=broken))
        await runtime.done()
        assert runtime.stopped
    assert (failed.status(), failed.status(0), failed.status(4)) == (1, 1, 4)
    assert [str(error) for error in failed.of(RuntimeError)] == ["broken"]
    assert failed.of(ValueError) == []

async def test_entries_given_as_then_are_loaded_once_the_rest_have_settled() -> None:
    # What an entrypoint does for whatever must come after everything else, such as the parser of a command line.
    seen: list[list[str]] = []

    def late(ctx: Context) -> None:
        seen.append(sorted(fiber.name for fiber in ctx.runtime.fibers() if fiber.state.value == "active"))

    async def slow(ctx: Context) -> None:
        await asyncio.sleep(0.01)

    outcome = await run([Entry("slow", lambda: Component("slow", apply=slow))],
                        then=[Entry("late", lambda: Component("late", apply=late))])
    assert outcome.status == 0
    assert seen == [["slow"]]

async def test_a_signal_asks_the_runtime_to_stop() -> None:
    log: list[str] = []
    async with open_runtime() as runtime:
        with stop_on_signals(runtime, (signal.SIGUSR1,)):
            await Loader(runtime.root).load([Entry("server", worker, config={"log": log, "name": "server"})])
            await runtime.settled()
            asyncio.get_running_loop().call_later(0.01, os.kill, os.getpid(), signal.SIGUSR1)
            await asyncio.wait_for(runtime.done(), 0.5)
            assert runtime.stopped
            assert log == ["server starts"]
    assert log == ["server starts", "server told to stop"]

async def test_a_scenario_runs_once_the_rest_has_settled_and_the_run_is_done_when_it_is() -> None:
    log: list[str] = []

    async def steps(ctx: Context) -> None:
        log.append("steps")

    outcome = await run_scenario([Entry("server", worker, config={"log": log, "name": "server"})], steps)
    assert (outcome.status, outcome.failures) == (0, ())
    assert log == ["server starts", "steps", "server told to stop"]

async def test_a_failure_in_the_steps_is_among_the_failures() -> None:
    async def steps(ctx: Context) -> None:
        raise AssertionError("not what was expected")

    outcome = await run_scenario([], steps)
    assert outcome.status == 1
    assert [(where, str(error)) for where, error in outcome.failures] == [("scenario", "not what was expected")]

async def test_a_scenario_that_cannot_start_says_what_is_missing() -> None:
    async def steps(ctx: Context) -> None:
        raise AssertionError("never runs")

    with pytest.raises(LookupError, match="the scenario never started: nothing provides exit_status"):
        await run_scenario([], steps, requires=(EXIT_STATUS,))

def test_a_composition_is_described_as_it_is_loaded() -> None:
    # Nothing is added to a composition to run it, so it is described exactly as given.
    log: list[str] = []
    assert describe([Entry("a", worker, config={"log": log, "name": "a"})]).startswith(
        "Activation order (unloaded in reverse):\n  1. a")
    assert log == []
