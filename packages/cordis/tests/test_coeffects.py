import pytest

from cordis import (
    Component,
    Context,
    FiberState,
    InactiveAccess,
    InactiveEffect,
    InsertionError,
    Key,
    ProvisionError,
    Runtime,
    UndeclaredAccess,
)

from .helpers import state

NUMBER: Key[int] = Key("number")
OTHER: Key[int] = Key("other")

def provider(value: int = 1, name: str = "provider", key: Key[int] = NUMBER) -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(key, value)
    return Component(name, apply=apply, provides=(key,))


async def test_reading_an_undeclared_key_is_refused(runtime: Runtime) -> None:
    # The coeffect specification is the capability (Algorithm 6): a key read is a key declared.
    seen: list[str] = []

    def nosy(ctx: Context) -> None:
        try:
            ctx.get(NUMBER)
        except UndeclaredAccess as error:
            seen.append(str(error))

    runtime.root.use(provider())
    runtime.root.use(Component("nosy", apply=nosy))
    await runtime.settled()
    assert seen == ["nosy reads number without requiring it"]

async def test_reading_a_declared_key_that_is_not_committed_is_refused(runtime: Runtime) -> None:
    fiber = runtime.root.use(Component("waiting", requires=(NUMBER,)))
    await runtime.settled()
    with pytest.raises(InactiveAccess, match="waiting requires number"):
        fiber.ctx.get(NUMBER)

async def test_find_probes_without_committing(runtime: Runtime) -> None:
    assert runtime.root.find(NUMBER) is None
    fiber = runtime.root.use(provider(7))
    assert runtime.root.find(NUMBER) is None  # not yet active, so not yet provided
    await runtime.settled()
    assert runtime.root.find(NUMBER) == 7
    await fiber.dispose()
    assert runtime.root.find(NUMBER) is None

async def test_a_key_outside_the_provision_cannot_be_provided(runtime: Runtime, failures: object) -> None:
    def apply(ctx: Context) -> None:
        ctx.provide(OTHER, 1)

    fiber = runtime.root.use(Component("overreaching", apply=apply, provides=(NUMBER,)))
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    assert isinstance(fiber.error, ProvisionError)

async def test_a_key_cannot_be_provided_twice(runtime: Runtime) -> None:
    # set(k, v) requires k ∉ dom(σ); the violation raises and no transition happens.
    def apply(ctx: Context) -> None:
        ctx.provide(NUMBER, 1)
        ctx.provide(NUMBER, 2)

    fiber = runtime.root.use(Component("twice", apply=apply, provides=(NUMBER,)))
    await runtime.settled()
    assert isinstance(fiber.error, ProvisionError)
    assert fiber.table == {}

async def test_a_second_provider_of_a_key_is_refused_at_insertion(runtime: Runtime) -> None:
    # The single-source discipline of O-Insert: each key has one possible provider, fixed by the provisions.
    runtime.root.use(provider(name="first"))
    with pytest.raises(InsertionError, match="second provides number, which first already provides"):
        runtime.root.use(provider(name="second"))

async def test_a_provider_may_be_replaced_once_the_old_one_has_left(runtime: Runtime) -> None:
    first = runtime.root.use(provider(1, name="first"))
    await first.dispose()
    runtime.root.use(provider(2, name="second"))
    await runtime.settled()
    assert runtime.root.find(NUMBER) == 2

async def test_a_component_cannot_both_require_and_provide_a_key() -> None:
    with pytest.raises(ValueError, match="both requires and provides number"):
        Component("loop", requires=(NUMBER,), provides=(NUMBER,))

async def test_an_effect_on_a_fiber_that_is_not_running_is_refused(runtime: Runtime) -> None:
    fiber = runtime.root.use(Component("waiting", requires=(NUMBER,)))
    await runtime.settled()
    with pytest.raises(InactiveEffect, match="waiting is inactive"):
        fiber.ctx.effect(lambda: None)

async def test_keys_with_the_same_name_are_different_keys(runtime: Runtime) -> None:
    namesake: Key[int] = Key("number")
    runtime.root.use(provider(1))
    runtime.root.use(provider(2, name="namesake", key=namesake))
    await runtime.settled()
    assert runtime.root.find(NUMBER) == 1
    assert runtime.root.find(namesake) == 2
