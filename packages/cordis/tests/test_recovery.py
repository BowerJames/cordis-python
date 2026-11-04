import asyncio
import itertools
from typing import AsyncIterator, Iterator

from cordis import Component, Context, Effect, Entries, FiberState, Inverse, Key, Runtime

LISTENERS: Key[Entries[str]] = Key("listeners")

def registry(entries: Entries[str]) -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(LISTENERS, entries)
    return Component("registry", apply=apply, provides=(LISTENERS,))

def listener(name: str, *values: str) -> Component:
    # Each registration is an effect whose inverse removes its own entry and no other.
    def register(ctx: Context, value: str) -> None:
        ctx.effect(lambda: ctx.get(LISTENERS).add(value))

    def apply(ctx: Context) -> None:
        for value in values:
            register(ctx, value)
    return Component(name, apply=apply, requires=(LISTENERS,))


async def test_unloading_a_component_recovers_the_context_it_began_at(runtime: Runtime) -> None:
    world: list[str] = []

    def apply(ctx: Context) -> Iterator[Inverse]:
        world.append("socket")
        yield lambda: world.remove("socket")
        world.append("timer")
        yield lambda: world.remove("timer")

    fiber = runtime.root.use(Component("worker", apply=apply))
    await runtime.settled()
    assert world == ["socket", "timer"]
    await fiber.dispose()
    assert world == []

async def test_neighbours_are_untouched_by_an_unload_between_their_effects(runtime: Runtime) -> None:
    # Recovery exactness (Theorem 68): the state after unloading one fiber is the state the other fibers' steps
    # alone would have reached.
    entries: Entries[str] = Entries()
    runtime.root.use(registry(entries))
    a = runtime.root.use(listener("a", "a1", "a2"))
    await runtime.settled()
    runtime.root.use(listener("b", "b1"))
    await runtime.settled()
    await a.dispose()
    assert list(entries) == ["b1"]

async def test_equal_registrations_from_two_components_are_withdrawn_independently(runtime: Runtime) -> None:
    # A commutative key (Definition 46): each addition draws an entry of its own, so equal values stay independent.
    entries: Entries[str] = Entries()
    runtime.root.use(registry(entries))
    a = runtime.root.use(listener("a", "same"))
    b = runtime.root.use(listener("b", "same"))
    await runtime.settled()
    assert list(entries) == ["same", "same"]
    await a.dispose()
    assert list(entries) == ["same"]
    await b.dispose()
    assert list(entries) == []

async def test_independent_components_unload_in_any_order_to_the_same_state(runtime: Runtime) -> None:
    # Theorem 43: pairwise-independent effects revert in any order and reach the state they began at.
    entries: Entries[str] = Entries()
    runtime.root.use(registry(entries))
    await runtime.settled()
    for order in itertools.permutations(range(3)):
        fibers = [runtime.root.use(listener(f"l{index}", f"v{index}", f"w{index}")) for index in range(3)]
        await runtime.settled()
        for index in order:
            await fibers[index].dispose()
            assert all(not value.endswith(str(index)) for value in entries)
        assert list(entries) == []

async def test_an_effect_disposed_early_is_never_reverted_again(runtime: Runtime) -> None:
    log: list[str] = []
    effects: list[Effect] = []

    def apply(ctx: Context) -> None:
        effects.append(ctx.effect(lambda: lambda: log.append("undone")))

    fiber = runtime.root.use(Component("early", apply=apply))
    await runtime.settled()
    await effects[0].dispose()
    await effects[0].dispose()
    await fiber.dispose()
    assert log == ["undone"]

async def test_effects_performed_while_active_are_reverted_with_the_rest(runtime: Runtime) -> None:
    log: list[str] = []
    contexts: list[Context] = []

    def apply(ctx: Context) -> Inverse:
        contexts.append(ctx)
        return lambda: log.append("activation undone")

    fiber = runtime.root.use(Component("late", apply=apply))
    await runtime.settled()
    contexts[0].effect(lambda: lambda: log.append("later effect undone"))
    await fiber.dispose()
    assert log == ["later effect undone", "activation undone"]

async def test_an_async_effect_is_waited_for_before_its_fiber_is_active(runtime: Runtime) -> None:
    log: list[str] = []

    async def connect() -> AsyncIterator[Inverse]:
        await asyncio.sleep(0)
        log.append("connected")
        yield lambda: log.append("disconnected")

    def apply(ctx: Context) -> None:
        ctx.effect(connect)

    fiber = runtime.root.use(Component("client", apply=apply))
    await runtime.wait_for(lambda: fiber.state is FiberState.ACTIVE)
    assert log == ["connected"]
    await fiber.dispose()
    assert log == ["connected", "disconnected"]

async def test_disposing_an_async_effect_halts_it_at_the_next_seam(runtime: Runtime) -> None:
    log: list[str] = []
    gate = asyncio.Event()
    holder: list[Context] = []

    async def steps() -> AsyncIterator[Inverse]:
        log.append("first")
        yield lambda: log.append("undo first")
        await gate.wait()
        log.append("second")
        yield lambda: log.append("undo second")
        log.append("third")

    runtime.root.use(Component("host", apply=holder.append))
    await runtime.settled()
    effect = holder[0].effect(steps)
    await asyncio.sleep(0)
    disposal = asyncio.ensure_future(effect.dispose())
    await asyncio.sleep(0)
    gate.set()
    await disposal
    assert log == ["first", "second", "undo second", "undo first"]
