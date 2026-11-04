import random

from cordis import Component, Context, Fiber, FiberState, Inverse, Key, Runtime, open_runtime
from cordis.testing import Failures

from .helpers import state

A: Key[str] = Key("a")
B: Key[str] = Key("b")
C: Key[str] = Key("c")

def node(name: str, requires: tuple[Key[str], ...], provides: tuple[Key[str], ...], world: set[str]) -> Component:
    def apply(ctx: Context) -> Inverse:
        inputs = ",".join(ctx.get(key) for key in requires)
        for key in provides:
            ctx.provide(key, f"{name}({inputs})")
        world.add(name)
        return lambda: world.discard(name)
    return Component(name, apply=apply, requires=requires, provides=provides)

def system(world: set[str]) -> list[Component]:
    return [
        node("source", (), (A,), world),
        node("middle", (A,), (B,), world),
        node("sink", (A, B), (C,), world),
        node("reader", (C,), (), world),
        node("orphan", (Key("missing"),), (), world),
    ]

async def snapshot(runtime: Runtime) -> tuple[frozenset[tuple[str, FiberState]], str | None]:
    await runtime.settled()
    assert runtime.quiet()
    return frozenset((fiber.name, fiber.state) for fiber in runtime.fibers()), runtime.root.find(C)


async def test_the_quiescent_state_does_not_depend_on_the_order_of_insertion() -> None:
    # Confluence (Theorem 80): any order of the same orchestration steps reaches the state a load from scratch does.
    outcomes = set()
    for seed in range(12):
        world: set[str] = set()
        components = system(world)
        random.Random(seed).shuffle(components)
        async with open_runtime(on_error=Failures()) as runtime:
            for component in components:
                runtime.root.use(component)
            outcomes.add((await snapshot(runtime), frozenset(world)))
    assert len(outcomes) == 1
    ((states, value), active) = outcomes.pop()
    assert value == "sink(source(),middle(source()))"
    assert active == {"source", "middle", "sink", "reader"}
    assert ("orphan", FiberState.INACTIVE) in states

async def test_reconfiguring_a_running_system_ends_where_a_cold_start_would() -> None:
    # Retiring and reinserting the source in the middle of a run, whatever else is in flight, ends in the same state.
    for seed in range(8):
        rng = random.Random(seed)
        world: set[str] = set()
        async with open_runtime(on_error=Failures()) as runtime:
            fibers: dict[str, Fiber] = {}
            for component in system(world):
                fibers[component.name] = runtime.root.use(component)
            for _ in range(3):
                if rng.random() < 0.5:
                    await runtime.settled()
                await fibers["source"].dispose()
                fibers["source"] = runtime.root.use(node("source", (), (A,), world))
            reconfigured = (await snapshot(runtime), frozenset(world))
        assert world == set()  # closing the runtime reverted everything
        cold: set[str] = set()
        async with open_runtime(on_error=Failures()) as runtime:
            for component in system(cold):
                runtime.root.use(component)
            assert reconfigured == (await snapshot(runtime), frozenset(cold))

async def test_a_dependency_cycle_leaves_its_members_inactive_without_deadlock(runtime: Runtime) -> None:
    # Section 6.5: a cycle is never satisfied, which is predictable from the declarations, and nothing waits on it.
    world: set[str] = set()
    left = runtime.root.use(node("left", (A,), (B,), world))
    right = runtime.root.use(node("right", (B,), (A,), world))
    await runtime.settled()
    assert state(left) is FiberState.INACTIVE
    assert state(right) is FiberState.INACTIVE
    assert runtime.quiet()

async def test_every_fiber_leaves_when_the_runtime_closes() -> None:
    world: set[str] = set()
    async with open_runtime(on_error=Failures()) as runtime:
        for component in system(world):
            runtime.root.use(component)
        await runtime.settled()
        assert world
    assert world == set()
    assert runtime.fibers() == ()
