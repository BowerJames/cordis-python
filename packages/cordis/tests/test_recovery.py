import asyncio
import itertools
from typing import AsyncIterator, Iterator

from cordis import Component, Context, Entries, FiberState, Inverse, Key, Runtime, Undo, UnyieldedInverse
from cordis.testing import Failures

from .helpers import state

LISTENERS: Key[Entries[str]] = Key("listeners")
NUMBER: Key[int] = Key("number")

def registry(entries: Entries[str]) -> Component[None]:
    def apply(ctx: Context) -> Inverse:
        return ctx.provide(LISTENERS, entries)
    return Component("registry", apply=apply, provides=(LISTENERS,))

def listener(name: str, *values: str) -> Component[None]:
    # Each registration returns the inverse that removes its own entry and no other, and the listener yields it.
    def apply(ctx: Context) -> Iterator[Inverse]:
        for value in values:
            yield ctx.get(LISTENERS).add(value)
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

async def test_every_yield_is_a_seam_the_runtime_takes_an_inverse_at(runtime: Runtime) -> None:
    # The accumulator holds exactly what the effect function yielded, in the order it yielded it.
    log: list[str] = []
    seen: list[int] = []

    def apply(ctx: Context) -> Iterator[Inverse]:
        for step in ("first", "second", "third"):
            seen.append(len(ctx.fiber.accumulator))
            yield lambda step=step: log.append(f"undo {step}")  # type: ignore[misc]

    fiber = runtime.root.use(Component("stepped", apply=apply))
    await runtime.settled()
    assert seen == [0, 1, 2]
    await fiber.dispose()
    assert log == ["undo third", "undo second", "undo first"]

async def test_an_inverse_left_unyielded_fails_the_activation_and_is_still_applied(
    runtime: Runtime, failures: Failures,
) -> None:
    # Nothing is taken silently: provide gives its inverse back, and an effect function that does not yield it has
    # not said where in its teardown the binding goes. The runtime fails it, and reverts the provision all the same.
    def forgetful(ctx: Context) -> None:
        ctx.provide(NUMBER, 1)

    fiber = runtime.root.use(Component("forgetful", apply=forgetful, provides=(NUMBER,)))
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    assert isinstance(fiber.error, UnyieldedInverse)
    assert failures.reported == [
        ("forgetful", "forgetful did not yield the inverse of provide number before its next step")]
    assert fiber.table == {}
    assert runtime.root.find(NUMBER) is None

async def test_an_inverse_is_yielded_before_the_next_seam(runtime: Runtime, failures: Failures) -> None:
    # At every seam the accumulator must hold the inverse of everything done so far, or stopping there would strand
    # an effect. So an inverse is yielded as soon as it is made, not kept for later.
    log: list[str] = []

    def late(ctx: Context) -> Iterator[Inverse]:
        child = ctx.use(Component("child", apply=lambda inner: log.append("child up")))
        yield lambda: log.append("undone")
        yield child.retire

    fiber = runtime.root.use(Component("late", apply=late))
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    assert failures.reported == [("late", "late did not yield the inverse of use child before its next step")]
    assert "undone" in log
    assert runtime.children(fiber) == ()

async def test_a_registration_left_unyielded_fails_the_activation_and_is_withdrawn(
    runtime: Runtime, failures: Failures,
) -> None:
    # Ownership comes from the step, not from the operation: an Undo made by a registry the component reached through
    # a key is owed by the component as surely as one made by provide.
    entries: Entries[str] = Entries()

    def careless(ctx: Context) -> None:
        ctx.get(LISTENERS).add("left behind", "listen for messages")

    runtime.root.use(registry(entries))
    fiber = runtime.root.use(Component("careless", apply=careless, requires=(LISTENERS,)))
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    assert failures.reported == [
        ("careless", "careless did not yield the inverse of listen for messages before its next step")]
    assert list(entries) == []

async def test_what_a_helper_makes_during_a_step_is_owed_by_the_fiber(runtime: Runtime, failures: Failures) -> None:
    # A helper performing an effect makes its Undo directly, with nothing to tell the runtime: the step it runs in does.
    world: list[str] = []

    def open_watcher(name: str) -> Undo:
        world.append(name)
        return Undo(lambda: world.remove(name), f"watch {name}")

    def kept(ctx: Context) -> Iterator[Inverse]:
        yield open_watcher("kept")

    def dropped(ctx: Context) -> Iterator[Inverse]:
        open_watcher("dropped")
        yield lambda: None

    good = runtime.root.use(Component("kept", apply=kept))
    bad = runtime.root.use(Component("dropped", apply=dropped))
    await runtime.settled()
    assert state(good) is FiberState.ACTIVE
    assert state(bad) is FiberState.FAILED
    assert world == ["kept"]
    assert failures.reported == [("dropped", "dropped did not yield the inverse of watch dropped before its next step")]

async def test_a_task_started_during_a_step_does_not_carry_the_step_on(runtime: Runtime, failures: Failures) -> None:
    # Work a component starts runs past the step that started it, and what it makes later is nobody's until adopted.
    made: list[Undo] = []
    gate = asyncio.Event()

    def apply(ctx: Context) -> Iterator[Inverse]:
        async def later() -> None:
            await gate.wait()
            made.append(Undo(lambda: None, "made later"))
            ctx.adopt(made[0])

        task = asyncio.get_running_loop().create_task(later())

        def stop() -> None:
            task.cancel()

        yield stop

    fiber = runtime.root.use(Component("worker", apply=apply))
    await runtime.settled()
    gate.set()
    while not made:
        await asyncio.sleep(0)
    assert fiber.outstanding() == []
    await fiber.dispose()
    assert failures.reported == []

async def test_an_inverse_yielded_twice_is_held_once(runtime: Runtime) -> None:
    def twice(ctx: Context) -> Iterator[Inverse]:
        undo = ctx.provide(NUMBER, 1)
        yield undo
        yield undo

    fiber = runtime.root.use(Component("twice", apply=twice, provides=(NUMBER,)))
    await runtime.settled()
    # Already on the accumulator, so it stays where it is: yielding it again changes nothing.
    assert state(fiber) is FiberState.ACTIVE
    assert len(fiber.accumulator) == 1

async def test_an_effect_undone_early_is_never_reverted_again(runtime: Runtime) -> None:
    log: list[str] = []
    undos: list[Undo] = []

    def apply(ctx: Context) -> Iterator[Inverse]:
        undos.append(Undo(lambda: log.append("undone"), "an early effect"))
        yield undos[0]
        yield lambda: log.append("the rest undone")

    fiber = runtime.root.use(Component("early", apply=apply))
    await runtime.settled()
    undos[0]()
    undos[0]()
    assert len(fiber.accumulator) == 1
    await fiber.dispose()
    assert log == ["undone", "the rest undone"]

async def test_effects_performed_while_active_are_adopted_and_reverted_with_the_rest(runtime: Runtime) -> None:
    log: list[str] = []
    contexts: list[Context] = []

    def apply(ctx: Context) -> Inverse:
        contexts.append(ctx)
        return lambda: log.append("activation undone")

    fiber = runtime.root.use(Component("late", apply=apply))
    await runtime.settled()
    contexts[0].adopt(lambda: log.append("later effect undone"))
    await fiber.dispose()
    assert log == ["later effect undone", "activation undone"]

async def test_an_inverse_never_adopted_is_reported_and_applied_as_the_fiber_unloads(
    runtime: Runtime, failures: Failures,
) -> None:
    log: list[str] = []
    contexts: list[Context] = []
    fiber = runtime.root.use(Component("careless", apply=contexts.append))
    await runtime.settled()
    child = contexts[0].use(Component("child", apply=lambda inner: lambda: log.append("child down")))
    await runtime.settled()
    await fiber.dispose()
    assert failures.reported == [("careless", "careless never yielded or adopted the inverse of use child")]
    assert log == ["child down"]
    assert child.removed

async def test_an_async_step_is_waited_for_before_its_fiber_is_active(runtime: Runtime) -> None:
    log: list[str] = []

    async def apply(ctx: Context) -> AsyncIterator[Inverse]:
        await asyncio.sleep(0)
        log.append("connected")
        yield lambda: log.append("disconnected")

    fiber = runtime.root.use(Component("client", apply=apply))
    await runtime.wait_for(lambda: fiber.state is FiberState.ACTIVE)
    assert log == ["connected"]
    await fiber.dispose()
    assert log == ["connected", "disconnected"]

async def test_the_root_holds_what_it_instantiates(runtime: Runtime) -> None:
    # The root is the orchestrator, with no effect function to yield from: closing the runtime retires its fibers.
    fiber = runtime.root.use(Component("top"))
    assert not fiber.retire.outstanding
    assert len(runtime.root_fiber.accumulator) == 1
    await fiber.dispose()
    assert len(runtime.root_fiber.accumulator) == 0
