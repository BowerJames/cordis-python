from typing import Iterator

from cordis import Component, Context, FiberState, Inverse, Key, Runtime
from cordis.testing import Failures

from .helpers import state

NUMBER: Key[int] = Key("number")

def provider(value: int = 1, name: str = "provider") -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(NUMBER, value)
    return Component(name, apply=apply, provides=(NUMBER,))


async def test_a_failing_activation_installs_nothing(runtime: Runtime, failures: Failures) -> None:
    # Section 4.4: a raise leaves by the route every deactivation takes, with what it had done so far.
    world: list[str] = []

    def apply(ctx: Context) -> Iterator[Inverse]:
        world.append("opened")
        yield lambda: world.remove("opened")
        ctx.provide(NUMBER, 1)
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

    def parent(ctx: Context) -> None:
        ctx.use(Component("child", apply=broken))
        ctx.use(provider())

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

async def test_a_failing_async_effect_fails_the_activation_it_belongs_to(runtime: Runtime) -> None:
    world: list[str] = []

    async def connect() -> Inverse:
        raise ConnectionError("refused")

    def apply(ctx: Context) -> Inverse:
        world.append("prepared")
        ctx.effect(connect)
        return lambda: world.remove("prepared")

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
