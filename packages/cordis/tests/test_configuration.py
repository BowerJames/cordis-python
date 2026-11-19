import asyncio
from dataclasses import dataclass
from typing import Iterator

import pytest

from cordis import Component, Context, FiberState, Inverse, Key, Runtime
from cordis.testing import Failures

# Configuration as Algorithm 4 has it: the component is applied to the payload its fiber was instantiated with, and a
# running fiber handed a new payload takes it in place or is revised (Section 5.2.1).

GREETING: Key[str] = Key("greeting")
READY: Key[bool] = Key("ready")

@dataclass(frozen=True)
class Greeting:
    text: str = "hello"

def greeter(log: list[str], accepting: bool | None = None) -> Component[Greeting]:
    # Provides its greeting. With accepting given, it says it takes a new configuration in place (True), or declines
    # (False); without, it says nothing, and every new configuration is a revision.
    def apply(ctx: Context, config: Greeting) -> Iterator[Inverse]:
        log.append(f"up with {config.text}")
        yield ctx.provide(GREETING, config.text)
        yield lambda: log.append(f"down with {config.text}")
        if accepting is not None:
            def accept(new: Greeting) -> bool:
                log.append(f"offered {new.text}")
                return accepting
            yield ctx.accept(accept)
    return Component("greeter", apply, config=Greeting, provides=(GREETING,))

def listener(heard: list[str]) -> Component[None]:
    def apply(ctx: Context) -> Inverse:
        heard.append(ctx.get(GREETING))
        return lambda: heard.append("gone")
    return Component("listener", apply, requires=(GREETING,))


async def test_the_effect_function_is_applied_to_the_configuration_given(runtime: Runtime) -> None:
    log: list[str] = []
    fiber = runtime.root.use(greeter(log), Greeting("hi"))
    await runtime.settled()
    assert log == ["up with hi"]
    assert fiber.config == Greeting("hi")

async def test_without_a_configuration_the_schema_gives_its_defaults(runtime: Runtime) -> None:
    log: list[str] = []
    runtime.root.use(greeter(log))
    await runtime.settled()
    assert log == ["up with hello"]

def test_the_schema_checks_a_configuration_and_a_component_without_one_takes_none() -> None:
    component = greeter([])
    assert component.configure({"text": "hey"}) == Greeting("hey")
    with pytest.raises(TypeError):
        component.configure({"colour": "red"})
    plain = listener([])
    assert plain.configure() is None
    with pytest.raises(TypeError, match="takes no configuration, but was given text"):
        plain.configure({"text": "hey"})

async def test_an_equal_configuration_changes_nothing(runtime: Runtime) -> None:
    log: list[str] = []
    fiber = runtime.root.use(greeter(log), Greeting("hi"))
    await runtime.settled()
    assert await fiber.update(Greeting("hi")) is fiber
    assert log == ["up with hi"]

async def test_a_fiber_that_accepts_nothing_in_place_is_revised(runtime: Runtime) -> None:
    log: list[str] = []
    heard: list[str] = []
    fiber = runtime.root.use(greeter(log), Greeting("hi"))
    runtime.root.use(listener(heard))
    await runtime.settled()
    revised = await fiber.update(Greeting("hey"))
    await runtime.settled()
    # A new fiber, so its dependents reactivate against it, and the old one is gone.
    assert revised is not fiber and fiber.removed
    assert revised.config == Greeting("hey") and revised.state is FiberState.ACTIVE
    assert log == ["up with hi", "down with hi", "up with hey"]
    assert heard == ["hi", "gone", "hey"]

async def test_a_fiber_that_accepts_takes_the_configuration_in_place(runtime: Runtime) -> None:
    log: list[str] = []
    heard: list[str] = []
    fiber = runtime.root.use(greeter(log, accepting=True), Greeting("hi"))
    runtime.root.use(listener(heard))
    await runtime.settled()
    assert await fiber.update(Greeting("hey")) is fiber
    await runtime.settled()
    # Nothing reloaded: not the fiber, and not what depends on it.
    assert log == ["up with hi", "offered hey"]
    assert heard == ["hi"]
    assert fiber.config == Greeting("hey")

async def test_a_configuration_taken_in_place_is_the_one_a_reactivation_applies(runtime: Runtime) -> None:
    log: list[str] = []
    ready = Component("ready", lambda ctx: ctx.provide(READY, True), provides=(READY,))

    def apply(ctx: Context, config: Greeting) -> Iterator[Inverse]:
        log.append(f"up with {config.text}")
        yield lambda: log.append(f"down with {config.text}")
        yield ctx.accept(lambda new: True)

    gated = Component("gated", apply, config=Greeting, requires=(READY,))
    first = runtime.root.use(ready)
    fiber = runtime.root.use(gated, Greeting("hi"))
    await runtime.settled()
    assert await fiber.update(Greeting("hey")) is fiber
    # Its dependency leaves and comes back: the fiber unloads, and activates again with what it now holds.
    await first.dispose()
    runtime.root.use(ready)
    await runtime.settled()
    assert log == ["up with hi", "down with hi", "up with hey"]

async def test_a_fiber_that_declines_is_revised(runtime: Runtime) -> None:
    log: list[str] = []
    fiber = runtime.root.use(greeter(log, accepting=False), Greeting("hi"))
    await runtime.settled()
    revised = await fiber.update(Greeting("hey"))
    await runtime.settled()
    assert revised is not fiber
    assert log == ["up with hi", "offered hey", "down with hi", "up with hey"]
    # The acceptor was an effect of the old fiber, withdrawn as it unloaded.
    assert len(fiber.acceptors) == 0

async def test_an_acceptor_that_fails_is_reported_and_the_fiber_revised(runtime: Runtime, failures: Failures) -> None:
    def apply(ctx: Context, config: Greeting) -> Iterator[Inverse]:
        def accept(new: Greeting) -> bool:
            raise RuntimeError(f"cannot take {new.text}")
        yield ctx.accept(accept)

    fiber = runtime.root.use(Component("fussy", apply, config=Greeting), Greeting("hi"))
    await runtime.settled()
    revised = await fiber.update(Greeting("hey"))
    await runtime.settled()
    assert revised is not fiber and revised.state is FiberState.ACTIVE
    assert failures.reported == [("fussy", "cannot take hey")]

async def test_an_acceptor_may_take_its_time(runtime: Runtime) -> None:
    taken: list[str] = []

    def apply(ctx: Context, config: Greeting) -> Iterator[Inverse]:
        async def accept(new: Greeting) -> bool:
            await asyncio.sleep(0)
            taken.append(new.text)
            return True
        yield ctx.accept(accept)

    fiber = runtime.root.use(Component("patient", apply, config=Greeting), Greeting("hi"))
    await runtime.settled()
    assert await fiber.update(Greeting("hey")) is fiber
    assert taken == ["hey"]

async def test_an_inactive_fiber_takes_its_configuration_as_it_stands(runtime: Runtime) -> None:
    # It has installed nothing, so there is nothing to revise: it activates with the new payload once it can.
    log: list[str] = []

    def apply(ctx: Context, config: Greeting) -> None:
        log.append(f"up with {config.text}")

    waiting = runtime.root.use(Component("waiting", apply, config=Greeting, requires=(READY,)), Greeting("hi"))
    await runtime.settled()
    assert waiting.state is FiberState.INACTIVE
    assert await waiting.update(Greeting("hey")) is waiting
    runtime.root.use(Component("ready", lambda ctx: ctx.provide(READY, True), provides=(READY,)))
    await runtime.settled()
    assert log == ["up with hey"]

async def test_a_failed_fiber_is_retried_with_its_new_configuration(runtime: Runtime, failures: Failures) -> None:
    def apply(ctx: Context, config: Greeting) -> Inverse:
        if config.text == "bad":
            raise ValueError("bad greeting")
        return ctx.provide(GREETING, config.text)

    fiber = runtime.root.use(Component("strict", apply, config=Greeting, provides=(GREETING,)), Greeting("bad"))
    await runtime.settled()
    assert fiber.state is FiberState.FAILED
    revised = await fiber.update(Greeting("fine"))
    await runtime.settled()
    assert revised.state is FiberState.ACTIVE and runtime.lookup(GREETING) == "fine"
