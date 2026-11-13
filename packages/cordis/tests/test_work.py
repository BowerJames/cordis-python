import asyncio
from typing import Iterator

from cordis import Component, Context, Effect, Fiber, FiberState, Inverse, Key, Runtime, Work, open_runtime
from cordis.testing import Failures

from .helpers import state

ADDRESS: Key[str] = Key("address")

def worker(log: list[str], name: str, work_for: float | None = None, hold: bool = True, grace: float | None = None,
           requires: tuple[Key[str], ...] = ()) -> Component:
    # Work that ends by itself after work_for, or runs until it is told to stop.
    def apply(ctx: Context) -> Iterator[Inverse]:
        async def work(stopping: asyncio.Event) -> None:
            log.append(f"{name} starts")
            try:
                await asyncio.wait_for(stopping.wait(), work_for)
                log.append(f"{name} told to stop")
            except TimeoutError:
                log.append(f"{name} finished")
        # Yielded before the work is started, so applied after the work has been stopped: newest first.
        yield lambda: log.append(f"{name} unloaded")
        ctx.spawn(work, name, hold=hold, grace=grace)
    return Component(name, apply=apply, requires=requires)

def working(work: Work, name: str = "working", hold: bool = True, grace: float | None = None) -> Component:
    def apply(ctx: Context) -> None:
        ctx.spawn(work, name, hold=hold, grace=grace)
    return Component(name, apply=apply)

async def forever(stopping: asyncio.Event) -> None:
    await stopping.wait()


async def test_a_runtime_is_done_once_it_has_settled_and_nothing_holds_it(runtime: Runtime) -> None:
    # What Node does by ending a process no handle keeps alive: whoever runs a composition waits for done.
    log: list[str] = []
    runtime.root.use(worker(log, "a", work_for=0.01))
    runtime.root.use(worker(log, "b", work_for=0.02))
    assert runtime.held() == ()
    await runtime.done()
    assert log == ["a starts", "b starts", "a finished", "b finished"]
    assert runtime.held() == ()

async def test_a_runtime_with_nothing_to_run_is_done_at_once(runtime: Runtime) -> None:
    await asyncio.wait_for(runtime.done(), 0.1)

async def test_work_that_does_not_hold_runs_beside_what_does_and_is_stopped_at_the_end(failures: Failures) -> None:
    log: list[str] = []
    async with open_runtime(on_error=failures) as runtime:
        runtime.root.use(worker(log, "ticker", hold=False))
        runtime.root.use(worker(log, "fetch", work_for=0.01))
        await runtime.done()
        assert log == ["ticker starts", "fetch starts", "fetch finished"]
        assert runtime.held() == ()
    # The two fibers rely on nothing of each other's, so they unload in no order; each one's work stops first.
    assert sorted(log[3:]) == ["fetch unloaded", "ticker told to stop", "ticker unloaded"]
    assert log.index("ticker told to stop") < log.index("ticker unloaded")
    assert failures.reported == []

async def test_settling_does_not_wait_for_work(runtime: Runtime) -> None:
    # Work is not a transition: a server serving for ever is still a settled system.
    fiber = runtime.root.use(working(forever, "server"))
    await asyncio.wait_for(runtime.settled(), 0.1)
    assert state(fiber) is FiberState.ACTIVE
    assert runtime.quiet()
    assert runtime.held() == ("server",)

async def test_unloading_a_fiber_stops_its_work_before_what_the_work_was_started_after(runtime: Runtime) -> None:
    # The work's inverse waits for it, so the inverses applied after it meet a state the work has left.
    log: list[str] = []

    def apply(ctx: Context) -> Iterator[Inverse]:
        log.append("socket bound")
        yield lambda: log.append("socket closed")

        async def serving(stopping: asyncio.Event) -> None:
            await stopping.wait()
            await asyncio.sleep(0.01)  # requests in progress finish
            log.append("stopped accepting")

        ctx.spawn(serving, "serving")

    fiber = runtime.root.use(Component("server", apply=apply))
    await runtime.settled()
    await fiber.dispose()
    assert log == ["socket bound", "stopped accepting", "socket closed"]
    assert runtime.held() == ()

async def test_closing_stops_work_in_the_order_of_what_relies_on_what(failures: Failures) -> None:
    # There is one order, the guard's: work that relies on a provider is stopped, and may still read it, before the
    # provider's own work is.
    log: list[str] = []

    def server(ctx: Context) -> None:
        ctx.provide(ADDRESS, "127.0.0.1:1")

        async def serving(stopping: asyncio.Event) -> None:
            await stopping.wait()
            log.append("server stopped accepting")

        ctx.spawn(serving, "serving")

    def announce(ctx: Context) -> None:
        async def announcing(stopping: asyncio.Event) -> None:
            log.append(f"listening on {ctx.get(ADDRESS)}")
            await stopping.wait()
            log.append(f"farewell from {ctx.get(ADDRESS)}")

        ctx.spawn(announcing, "announcing", hold=False)

    async with open_runtime(on_error=failures) as runtime:
        runtime.root.use(Component("announce", apply=announce, requires=(ADDRESS,)))
        runtime.root.use(Component("server", apply=server, provides=(ADDRESS,)))
        await runtime.settled()
        await asyncio.sleep(0)
    assert log == ["listening on 127.0.0.1:1", "farewell from 127.0.0.1:1", "server stopped accepting"]
    assert failures.reported == []

async def test_work_told_to_stop_is_cancelled_once_its_grace_runs_out(runtime: Runtime) -> None:
    log: list[str] = []

    async def stubborn(stopping: asyncio.Event) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            log.append("cancelled")
            raise

    fiber = runtime.root.use(working(stubborn, grace=0.01))
    await runtime.settled()
    await asyncio.sleep(0)
    await fiber.dispose()
    assert log == ["cancelled"]
    assert runtime.held() == ()

async def test_the_runtimes_grace_applies_to_work_that_names_none() -> None:
    log: list[str] = []

    async def stubborn(stopping: asyncio.Event) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            log.append("cancelled")
            raise

    async with open_runtime(grace=0.01) as runtime:
        runtime.root.use(working(stubborn))
        await runtime.settled()
        await asyncio.sleep(0)
    assert log == ["cancelled"]

async def test_work_that_raises_is_reported_at_its_fiber_which_stays_loaded(runtime: Runtime, failures: Failures) -> None:
    async def broken(stopping: asyncio.Event) -> None:
        raise RuntimeError("the work broke")

    fiber = runtime.root.use(working(broken, "broken"))
    await runtime.done()
    assert failures.reported == [("broken", "the work broke")]
    assert state(fiber) is FiberState.ACTIVE
    assert runtime.held() == ()

async def test_work_can_retire_its_own_fiber_and_return(runtime: Runtime) -> None:
    # Retiring is a request, so the work makes it and returns; waiting for its own fiber to leave would be waiting
    # for itself, since the fiber leaves only once its work has.
    log: list[str] = []

    def apply(ctx: Context) -> Inverse:
        async def once(stopping: asyncio.Event) -> None:
            ctx.fiber.retire()
            log.append("finished")
        ctx.spawn(once, "once")
        return lambda: log.append("unloaded")

    fiber = runtime.root.use(Component("once", apply=apply))
    await asyncio.wait_for(runtime.done(), 0.5)
    assert log == ["finished", "unloaded"]
    assert fiber.removed

async def test_work_that_ends_its_own_effect_is_not_waited_for_by_itself(runtime: Runtime) -> None:
    log: list[str] = []

    def apply(ctx: Context) -> None:
        async def once(stopping: asyncio.Event) -> None:
            await spawned.dispose()
            log.append(f"told to stop: {stopping.is_set()}")
        spawned = ctx.spawn(once, "once")

    runtime.root.use(Component("once", apply=apply))
    await asyncio.wait_for(runtime.done(), 0.5)
    assert log == ["told to stop: True"]

async def test_asked_to_stop_a_runtime_is_done_whatever_holds_it(runtime: Runtime) -> None:
    runtime.root.use(working(forever, "server"))
    waiting = asyncio.ensure_future(runtime.done())
    await runtime.settled()
    await asyncio.sleep(0)
    assert not waiting.done()
    assert not runtime.stopped
    runtime.stop()
    await asyncio.wait_for(waiting, 0.1)
    assert runtime.stopped
    # Stopping unloads nothing: whoever was waiting does that, by closing the runtime.
    assert runtime.held() == ("server",)

async def test_done_can_be_asked_of_one_fiber_and_what_it_instantiated(runtime: Runtime) -> None:
    # A composition names what it runs for: a client beside a server that would serve for ever.
    log: list[str] = []
    runtime.root.use(worker(log, "server"))

    def client(ctx: Context) -> None:
        ctx.use(worker(log, "client", work_for=0.01))

    fiber = runtime.root.use(Component("client group", apply=client))
    await runtime.settled()
    assert runtime.held(fiber) == ("client",)
    await asyncio.wait_for(runtime.done(under=fiber), 0.5)
    assert log == ["server starts", "client starts", "client finished"]
    assert runtime.held() == ("server",)

async def test_a_hold_keeps_the_runtime_until_it_is_let_go(runtime: Runtime) -> None:
    # Not everything that should keep a program running is a task: a timer yet to fire holds too.
    fired: list[str] = []

    def apply(ctx: Context) -> None:
        held = ctx.hold("timer")

        def fire() -> None:
            fired.append("fired")
            asyncio.ensure_future(held.dispose())

        handle = asyncio.get_running_loop().call_later(0.01, fire)
        ctx.effect(lambda: handle.cancel, "timer")

    runtime.root.use(Component("scheduler", apply=apply))
    await runtime.settled()
    assert runtime.held() == ("timer",)
    await runtime.done()
    assert fired == ["fired"]

async def test_unloading_a_fiber_lets_go_of_what_it_held(runtime: Runtime) -> None:
    def apply(ctx: Context) -> None:
        ctx.hold("open")

    fiber = runtime.root.use(Component("holder", apply=apply))
    await runtime.settled()
    assert runtime.held() == ("open",)
    await fiber.dispose()
    assert runtime.held() == ()
    await asyncio.wait_for(runtime.done(), 0.1)

async def test_failures_beneath_a_containing_fiber_go_to_its_handler(runtime: Runtime, failures: Failures) -> None:
    # A failure stays on its fiber (Section 4.4); containing says who is told of it, so one session can fail alone.
    contained: list[tuple[str, str]] = []

    def broken(ctx: Context) -> None:
        raise RuntimeError("only this part fails")

    async def breaking(stopping: asyncio.Event) -> None:
        raise RuntimeError("its work fails too")

    def session(ctx: Context) -> None:
        ctx.use(Component("part", apply=broken))
        ctx.use(working(breaking, "worker"))

    def host(ctx: Context) -> None:
        child = ctx.use(Component("session", apply=session))
        ctx.contain(lambda fiber, error: contained.append((fiber.name, str(error))), child)

    fiber = runtime.root.use(Component("host", apply=host))
    runtime.root.use(Component("outside", apply=broken))
    await runtime.done()
    assert sorted(contained) == [("part", "only this part fails"), ("worker", "its work fails too")]
    assert failures.reported == [("outside", "only this part fails")]
    assert state(fiber) is FiberState.ACTIVE

async def test_a_handler_that_raises_passes_the_failure_on(runtime: Runtime, failures: Failures) -> None:
    seen: list[str] = []

    def broken(ctx: Context) -> None:
        raise RuntimeError("broken")

    def host(ctx: Context) -> None:
        def handler(fiber: Fiber, error: Exception) -> None:
            seen.append(fiber.name)
            if fiber.name == "not mine":
                raise error

        ctx.contain(handler)
        ctx.use(Component("mine", apply=broken))
        ctx.use(Component("not mine", apply=broken))

    runtime.root.use(Component("host", apply=host))
    await runtime.settled()
    assert sorted(seen) == ["mine", "not mine"]
    assert failures.reported == [("not mine", "broken")]

async def test_containing_ends_with_the_effect_that_began_it(runtime: Runtime, failures: Failures) -> None:
    def broken(ctx: Context) -> None:
        raise RuntimeError("broken")

    contained: list[str] = []
    effects: list[Effect] = []
    contexts: list[Context] = []

    def host(ctx: Context) -> None:
        effects.append(ctx.contain(lambda fiber, error: contained.append(fiber.name)))
        contexts.append(ctx)

    runtime.root.use(Component("host", apply=host))
    await runtime.settled()
    contexts[0].use(Component("first", apply=broken))
    await runtime.settled()
    await effects[0].dispose()
    contexts[0].use(Component("second", apply=broken))
    await runtime.settled()
    assert contained == ["first"]
    assert failures.reported == [("second", "broken")]

async def test_work_is_recorded_before_it_runs_even_when_tasks_start_eagerly(runtime: Runtime) -> None:
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        log: list[str] = []
        fiber = runtime.root.use(worker(log, "a", work_for=0.01))
        await runtime.done()
        assert log == ["a starts", "a finished"]
        await fiber.dispose()
        assert log == ["a starts", "a finished", "a unloaded"]
    finally:
        loop.set_task_factory(previous)
