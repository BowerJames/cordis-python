import asyncio
from typing import AsyncIterator, Iterator

from cordis import Component, Context, FiberState, Inverse, Key, Runtime
from cordis.testing import Failures

from .helpers import state

NUMBER: Key[int] = Key("number")

def provider(value: int = 1, name: str = "provider") -> Component[None]:
    def apply(ctx: Context) -> Inverse:
        return ctx.provide(NUMBER, value)
    return Component(name, apply=apply, provides=(NUMBER,))


async def test_a_failing_activation_installs_nothing(runtime: Runtime, failures: Failures) -> None:
    # Section 4.4: a raise leaves by the route every deactivation takes, with what it had done so far.
    world: list[str] = []

    def apply(ctx: Context) -> Iterator[Inverse]:
        world.append("opened")
        yield lambda: world.remove("opened")
        yield ctx.provide(NUMBER, 1)
        raise RuntimeError("port already bound")

    fiber = runtime.root.use(Component("server", apply=apply, provides=(NUMBER,)))
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    assert world == []
    assert fiber.table == {}
    assert runtime.root.find(NUMBER) is None
    assert failures.reported == [("server", "port already bound")]

async def test_a_failure_stays_on_its_fiber(runtime: Runtime) -> None:
    def broken(ctx: Context) -> None:
        raise RuntimeError("broken")

    def parent(ctx: Context) -> Iterator[Inverse]:
        yield ctx.use(Component("child", apply=broken)).retire
        yield ctx.use(provider()).retire

    fiber = runtime.root.use(Component("parent", apply=parent))
    await runtime.settled()
    assert state(fiber) is FiberState.ACTIVE
    assert runtime.root.find(NUMBER) == 1
    assert {child.name: child.state for child in runtime.children(fiber)} == {
        "child": FiberState.FAILED,
        "provider": FiberState.ACTIVE,
    }

async def test_a_failed_fiber_is_not_retried_when_its_environment_changes(runtime: Runtime) -> None:
    # A retry is a revision: an effect function that raised is not run again against the same component.
    attempts: list[int] = []

    def flaky(ctx: Context) -> None:
        attempts.append(ctx.get(NUMBER))
        raise RuntimeError("flaky")

    first = runtime.root.use(provider(1, name="first"))
    fiber = runtime.root.use(Component("flaky", apply=flaky, requires=(NUMBER,)))
    await runtime.settled()
    await first.dispose()
    runtime.root.use(provider(2, name="second"))
    await runtime.settled()
    assert attempts == [1]
    assert state(fiber) is FiberState.FAILED
    assert runtime.quiet()

async def test_a_failing_async_step_fails_the_activation_it_belongs_to(runtime: Runtime) -> None:
    world: list[str] = []

    async def connect() -> Inverse:
        raise ConnectionError("refused")

    async def apply(ctx: Context) -> AsyncIterator[Inverse]:
        world.append("prepared")
        yield lambda: world.remove("prepared")
        yield await connect()

    fiber = runtime.root.use(Component("client", apply=apply))
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    assert str(fiber.error) == "refused"
    assert world == []

async def test_a_failing_inverse_is_reported_and_teardown_continues(runtime: Runtime, failures: Failures) -> None:
    world: list[str] = []

    def broken() -> None:
        raise RuntimeError("cannot close")

    def apply(ctx: Context) -> Iterator[Inverse]:
        world.append("a")
        yield lambda: world.remove("a")
        yield broken

    fiber = runtime.root.use(Component("sloppy", apply=apply))
    await runtime.settled()
    await fiber.dispose()
    assert world == []
    assert failures.reported == [("sloppy", "cannot close")]
    assert fiber.removed

async def test_an_active_fiber_that_fails_leaves_and_rests_failed(runtime: Runtime, failures: Failures) -> None:
    # Section 4.4's failure read at an active fiber: it stops providing, its dependents leave first, and it unloads.
    log: list[str] = []
    contexts: list[Context] = []

    def server(ctx: Context) -> Iterator[Inverse]:
        contexts.append(ctx)
        yield lambda: log.append("server down")
        yield ctx.provide(NUMBER, 1)

    def client(ctx: Context) -> Inverse:
        log.append(f"client up on {ctx.get(NUMBER)}")
        return lambda: log.append(f"client down, still reading {ctx.get(NUMBER)}")

    fiber = runtime.root.use(Component("server", apply=server, provides=(NUMBER,)))
    runtime.root.use(Component("client", apply=client, requires=(NUMBER,)))
    await runtime.settled()
    contexts[0].fail(RuntimeError("the socket died"))
    contexts[0].fail(RuntimeError("and again"))
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    assert str(fiber.error) == "the socket died"
    assert log == ["client up on 1", "client down, still reading 1", "server down"]
    assert failures.reported == [("server", "the socket died"), ("server", "and again")]

async def test_a_failure_during_activation_stops_it_at_the_next_seam(runtime: Runtime, failures: Failures) -> None:
    log: list[str] = []
    gate = asyncio.Event()

    async def apply(ctx: Context) -> AsyncIterator[Inverse]:
        yield lambda: log.append("undo first")
        ctx.fail(RuntimeError("background work broke"))
        await gate.wait()
        log.append("second")
        yield lambda: log.append("undo second")

    fiber = runtime.root.use(Component("slow", apply=apply))
    await asyncio.sleep(0)
    gate.set()
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    assert log == ["second", "undo second", "undo first"]
