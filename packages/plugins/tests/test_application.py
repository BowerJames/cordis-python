import asyncio

from cordis import Component, Context, Inverse
from cordis_loader import Entry
from plugins import APPLICATION, Application, describe, run_application, serve


def ender(status: int = 0, after: float = 0.01) -> Component:
    # Ends the application a moment after it has started, once the work it is testing is under way.
    def apply(ctx: Context) -> None:
        async def end(stopping: asyncio.Event) -> None:
            await asyncio.sleep(after)
            ctx.get(APPLICATION).end(status)
        serve(ctx, "ender", end)
    return Component("ender", apply=apply, requires=(APPLICATION,))

def worker(log: list[str], name: str, work_for: float = 0.0, fail: bool = False) -> Component:
    def apply(ctx: Context) -> Inverse:
        async def work(stopping: asyncio.Event) -> None:
            log.append(f"{name} starts")
            if fail:
                raise RuntimeError(f"{name} broke")
            try:
                await asyncio.wait_for(stopping.wait(), work_for)
                log.append(f"{name} told to stop")
            except TimeoutError:
                log.append(f"{name} done")
        serve(ctx, name, work)
        return lambda: log.append(f"{name} unloaded")
    return Component(name, apply=apply, requires=(APPLICATION,))

async def test_an_application_ends_by_itself_once_nothing_holds_it() -> None:
    log: list[str] = []
    outcome = await run_application([Entry("a", worker, config={"log": log, "name": "a"}),
                                     Entry("b", worker, config={"log": log, "name": "b", "work_for": 0.01})], signals=False)
    assert outcome.status == 0
    assert log[-4:-2] == ["a done", "b done"]
    assert sorted(log[-2:]) == ["a unloaded", "b unloaded"]

async def test_nothing_to_run_ends_at_once() -> None:
    assert (await run_application([], signals=False)).status == 0

async def test_ending_stops_every_served_task_before_anything_unloads() -> None:
    # The first phase of ending tells every served task to stop while every fiber is still loaded.
    log: list[str] = []
    outcome = await run_application([
        Entry("a", worker, config={"log": log, "name": "a", "work_for": 10}),
        Entry("b", worker, config={"log": log, "name": "b", "work_for": 10}),
        Entry("ender", ender, config={"status": 3}),
    ], signals=False)
    assert outcome.status == 3
    assert sorted(log[:2]) == ["a starts", "b starts"]
    assert sorted(log[2:4]) == ["a told to stop", "b told to stop"]
    assert sorted(log[4:]) == ["a unloaded", "b unloaded"]

async def test_a_served_task_that_fails_ends_the_application_with_status_1() -> None:
    log: list[str] = []
    outcome = await run_application([
        Entry("steady", worker, config={"log": log, "name": "steady", "work_for": 10}),
        Entry("broken", worker, config={"log": log, "name": "broken", "fail": True}),
    ], signals=False)
    assert outcome.status == 1
    [(where, error)] = outcome.failures
    assert (where, str(error), error.__notes__) == ("broken", "broken broke", ["in broken"])
    assert "steady told to stop" in log

async def test_a_component_that_fails_to_activate_ends_the_application() -> None:
    def broken(ctx: Context) -> None:
        raise ValueError("misconfigured")
    outcome = await run_application([Entry("broken", lambda: Component("broken", apply=broken))], signals=False)
    assert outcome.status == 1
    assert [str(error) for _, error in outcome.failures] == ["misconfigured"]

async def test_a_contained_failure_goes_to_its_handler_and_the_application_runs_on() -> None:
    contained: list[str] = []

    def host(ctx: Context) -> None:
        def broken(inner: Context) -> None:
            raise RuntimeError("only this part fails")
        app = ctx.get(APPLICATION)
        child = ctx.use(Component("part", apply=broken))
        app.contain(child, lambda error, where: contained.append(f"{error} in {where}"))
        app.when_started(lambda: app.end(0))

    outcome = await run_application([Entry("host", lambda: Component("host", apply=host, requires=(APPLICATION,)))],
                                     signals=False)
    assert outcome.status == 0
    assert contained == ["only this part fails in part"]

async def test_started_callbacks_run_once_everything_has_settled() -> None:
    app = Application()
    seen: list[str] = []
    app.when_started(lambda: seen.append("first"))
    app.when_started(lambda: seen.append("second"))
    withdraw = app.when_started(lambda: seen.append("withdrawn"))
    withdraw()
    assert seen == []
    app.start()
    app.when_started(lambda: seen.append("late"))
    assert seen == ["first", "second", "late"]

async def test_work_told_to_stop_is_cancelled_once_its_grace_runs_out() -> None:
    log: list[str] = []

    def stubborn(ctx: Context) -> None:
        async def work(stopping: asyncio.Event) -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                log.append("cancelled")
                raise
        serve(ctx, "stubborn", work)

    await run_application([Entry("stubborn", lambda: Component("stubborn", apply=stubborn, requires=(APPLICATION,))),
                           Entry("ender", ender)], grace=0.01, signals=False)
    assert log == ["cancelled"]

def test_an_application_is_described_with_the_component_providing_it() -> None:
    log: list[str] = []
    description = describe([Entry("a", worker, config={"log": log, "name": "a"})])
    assert description.startswith("Activation order (unloaded in reverse):\n  1. application\n  2. a           after application")
    assert log == []
