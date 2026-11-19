import asyncio
from typing import Iterator

from cordis import Component, Context, Inverse, Key
from cordis_loader import Entry
from cordis_plugins import APPLICATION, ApplicationRun, background, describe, run_application

SHARED: Key[str] = Key("shared")

def ender(status: int = 0, after: float = 0.01) -> Component[None]:
    # Ends the application a moment after it has started, once the work it is testing is under way.
    def apply(ctx: Context) -> Inverse:
        async def end(stopping: asyncio.Event) -> None:
            if await ctx.get(APPLICATION).started():
                await asyncio.sleep(after)
                ctx.get(APPLICATION).end(status)
        return background(ctx, "ender", end)
    return Component("ender", apply=apply, requires=(APPLICATION,))

def worker(log: list[str], name: str, work_for: float = 0.0, fail: bool = False, provides: bool = False,
           relies: bool = False) -> Component[None]:
    # Background work of its own, started once the application has, and stopped by the inverse it yields.
    def apply(ctx: Context) -> Iterator[Inverse]:
        async def work(stopping: asyncio.Event) -> None:
            if not await ctx.get(APPLICATION).started():
                return
            log.append(f"{name} starts")
            if fail:
                raise RuntimeError(f"{name} broke")
            try:
                await asyncio.wait_for(stopping.wait(), work_for)
                log.append(f"{name} told to stop")
            except TimeoutError:
                log.append(f"{name} done")
        yield lambda: log.append(f"{name} unloaded")
        if provides:
            yield ctx.provide(SHARED, name)
        yield background(ctx, name, work)
    return Component(name, apply=apply, requires=(APPLICATION, *((SHARED,) if relies else ())),
                     provides=(SHARED,) if provides else ())

async def test_an_application_runs_until_something_ends_it() -> None:
    # Work finishing does not end it: there is nothing to hold it open, only something to end it.
    log: list[str] = []
    outcome = await run_application([Entry("a", worker(log=log, name="a")),
                                     Entry("ender", ender(status=0, after=0.05))], signals=False)
    assert outcome.status == 0
    assert log == ["a starts", "a done", "a unloaded"]

async def test_ending_unloads_dependents_before_what_they_depend_on() -> None:
    # No phase of its own: closing the runtime unloads every fiber, and the guard on L-Unload orders them, so the
    # dependent stops its work and unloads while what it relies on is still there.
    log: list[str] = []
    outcome = await run_application([
        Entry("provider", worker(log=log, name="provider", work_for=10, provides=True)),
        Entry("dependent", worker(log=log, name="dependent", work_for=10, relies=True)),
        Entry("ender", ender(status=3)),
    ], signals=False)
    assert outcome.status == 3
    assert sorted(log[:2]) == ["dependent starts", "provider starts"]
    assert log[2:] == ["dependent told to stop", "dependent unloaded", "provider told to stop", "provider unloaded"]

async def test_work_that_fails_fails_its_fiber_and_ends_the_application_with_status_1() -> None:
    log: list[str] = []
    outcome = await run_application([
        Entry("steady", worker(log=log, name="steady", work_for=10)),
        Entry("broken", worker(log=log, name="broken", fail=True)),
    ], signals=False)
    assert outcome.status == 1
    [(where, error)] = outcome.failures
    assert (where, str(error), error.__notes__) == ("broken", "broken broke", ["in broken"])
    assert "steady told to stop" in log
    assert "broken unloaded" in log

async def test_a_component_that_fails_to_activate_ends_the_application() -> None:
    def broken(ctx: Context) -> None:
        raise ValueError("misconfigured")
    outcome = await run_application([Entry("broken", Component("broken", apply=broken))], signals=False)
    assert outcome.status == 1
    assert [str(error) for _, error in outcome.failures] == ["misconfigured"]

async def test_a_contained_failure_goes_to_its_handler_and_the_application_runs_on() -> None:
    contained: list[str] = []

    def host(ctx: Context) -> Iterator[Inverse]:
        def broken(inner: Context) -> None:
            raise RuntimeError("only this part fails")
        app = ctx.get(APPLICATION)
        child = ctx.use(Component("part", apply=broken))
        yield child.retire
        yield app.contain(child, lambda error, where: contained.append(f"{error} in {where}"))
        yield app.when_started(lambda: app.end(0))

    outcome = await run_application([Entry("host", Component("host", apply=host, requires=(APPLICATION,)))],
                                     signals=False)
    assert outcome.status == 0
    assert contained == ["only this part fails in part"]

async def test_started_callbacks_run_once_everything_has_settled() -> None:
    app = ApplicationRun()
    seen: list[str] = []
    held = [app.when_started(lambda: seen.append("first")), app.when_started(lambda: seen.append("second"))]
    withdraw = app.when_started(lambda: seen.append("withdrawn"))
    withdraw()
    assert seen == []
    app.start()
    held.append(app.when_started(lambda: seen.append("late")))
    assert seen == ["first", "second", "late"]
    for undo in held:
        undo()

async def test_work_told_to_stop_is_cancelled_once_its_grace_runs_out() -> None:
    log: list[str] = []

    def stubborn(ctx: Context) -> Inverse:
        async def work(stopping: asyncio.Event) -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                log.append("cancelled")
                raise
        return background(ctx, "stubborn", work, grace=0.01)

    await run_application([Entry("stubborn", Component("stubborn", apply=stubborn, requires=(APPLICATION,))),
                           Entry("ender", ender())], signals=False)
    assert log == ["cancelled"]

def test_an_application_is_described_with_the_component_providing_it() -> None:
    log: list[str] = []
    description = describe([Entry("a", worker(log=log, name="a"))])
    assert description.startswith("Activation order (unloaded in reverse):\n  1. application\n  2. a           after application")
    assert log == []
