import asyncio
from typing import AsyncIterator, Iterator

from cordis import Component, Context, FiberState, Inverse, Key, Runtime

from .helpers import state

DATABASE: Key[dict[str, str]] = Key("database")
CHAT: Key[str] = Key("chat")

def database(log: list[str], value: dict[str, str] | None = None, name: str = "database") -> Component:
    def apply(ctx: Context) -> None:
        log.append(f"{name} up")
        ctx.provide(DATABASE, value if value is not None else {"greeting": "hello"})
        ctx.effect(lambda: lambda: log.append(f"{name} down"))
    return Component(name, apply=apply, provides=(DATABASE,))

def reader(log: list[str]) -> Component:
    def apply(ctx: Context) -> Inverse:
        log.append(f"reader up: {ctx.get(DATABASE)['greeting']}")
        # Teardown reads the dependency whose departure caused it (Theorem 70).
        return lambda: log.append(f"reader down: {ctx.get(DATABASE)['greeting']}")
    return Component("reader", apply=apply, requires=(DATABASE,))


async def test_a_component_with_no_requirements_activates_at_once(runtime: Runtime) -> None:
    log: list[str] = []
    fiber = runtime.root.use(database(log))
    assert state(fiber) is FiberState.LOADING
    await runtime.settled()
    assert state(fiber) is FiberState.ACTIVE
    assert log == ["database up"]
    assert runtime.quiet()

async def test_insertion_order_does_not_matter(runtime: Runtime) -> None:
    # A fiber waits at L-Begin until its declared keys are provided: there is no boot order to arrange.
    log: list[str] = []
    consumer = runtime.root.use(reader(log))
    await runtime.settled()
    assert state(consumer) is FiberState.INACTIVE
    assert log == []
    provider = runtime.root.use(database(log))
    await runtime.settled()
    assert state(consumer) is FiberState.ACTIVE
    assert log == ["database up", "reader up: hello"]
    assert consumer.committed == {DATABASE: provider}

async def test_a_provider_leaves_only_after_its_consumers_have_finished_with_it(runtime: Runtime) -> None:
    # L-Leave stops providing at once; L-Unload runs the provider's inverses only once nobody relies on it.
    log: list[str] = []
    provider = runtime.root.use(database(log))
    consumer = runtime.root.use(reader(log))
    await runtime.settled()
    await provider.dispose()
    assert log == ["database up", "reader up: hello", "reader down: hello", "database down"]
    assert state(consumer) is FiberState.INACTIVE
    assert provider.removed

async def test_a_consumer_follows_its_dependency_away_and_back(runtime: Runtime) -> None:
    log: list[str] = []
    consumer = runtime.root.use(reader(log))
    first = runtime.root.use(database(log, {"greeting": "hello"}, name="first"))
    await runtime.settled()
    await first.dispose()
    assert state(consumer) is FiberState.INACTIVE
    second = runtime.root.use(database(log, {"greeting": "hi"}, name="second"))
    await runtime.settled()
    assert state(consumer) is FiberState.ACTIVE
    assert consumer.committed == {DATABASE: second}
    assert log[-2:] == ["second up", "reader up: hi"]

async def test_providers_are_compared_by_identity_not_by_value(runtime: Runtime) -> None:
    # A fresh provider of an equal value is still a different provider, so the consumer reactivates against it.
    log: list[str] = []
    same = {"greeting": "hello"}
    consumer = runtime.root.use(reader(log))
    first = runtime.root.use(database(log, same, name="first"))
    await runtime.settled()
    await first.dispose()
    runtime.root.use(database(log, same, name="second"))
    await runtime.settled()
    assert log.count("reader up: hello") == 2
    assert state(consumer) is FiberState.ACTIVE

async def test_dependents_unload_in_a_cascade_and_providers_go_last(runtime: Runtime) -> None:
    log: list[str] = []

    def chat(ctx: Context) -> Inverse:
        ctx.provide(CHAT, f"chat over {ctx.get(DATABASE)['greeting']}")
        log.append("chat up")
        return lambda: log.append("chat down")

    def history(ctx: Context) -> Inverse:
        log.append(f"history up: {ctx.get(CHAT)}")
        return lambda: log.append(f"history down: {ctx.get(CHAT)}, {ctx.get(DATABASE)['greeting']}")

    runtime.root.use(Component("history", apply=history, requires=(DATABASE, CHAT)))
    runtime.root.use(Component("chat", apply=chat, requires=(DATABASE,), provides=(CHAT,)))
    provider = runtime.root.use(database(log))
    await runtime.settled()
    assert log == ["database up", "chat up", "history up: chat over hello"]
    await provider.dispose()
    assert log[3:] == ["history down: chat over hello, hello", "chat down", "database down"]
    assert runtime.quiet()

async def test_an_activation_diverts_at_the_next_seam_when_its_dependency_leaves(runtime: Runtime) -> None:
    # L-Divert: the resolution changes mid-activation, so the fiber stops at the seam and unwinds what it did.
    log: list[str] = []
    started, gate = asyncio.Event(), asyncio.Event()

    async def slow(ctx: Context) -> AsyncIterator[Inverse]:
        log.append("step one")
        started.set()
        yield lambda: log.append("undo step one")
        await gate.wait()
        yield lambda: log.append("undo the wait")
        log.append("step two")
        yield lambda: log.append("undo step two")

    provider = runtime.root.use(database(log))
    await runtime.settled()
    consumer = runtime.root.use(Component("slow", apply=slow, requires=(DATABASE,)))
    await started.wait()
    provider.retire()
    gate.set()
    await runtime.settled()
    assert "step two" not in log
    assert log[-3:] == ["undo the wait", "undo step one", "database down"]
    assert state(consumer) is FiberState.INACTIVE
    assert consumer.committed is None

async def test_an_activation_runs_against_one_resolution_throughout(runtime: Runtime) -> None:
    # Theorem 71: every iteration reads the providers committed at L-Begin.
    log: list[str] = []
    seen: list[dict[str, str]] = []

    def steady(ctx: Context) -> Iterator[None]:
        seen.append(ctx.get(DATABASE))
        yield None
        seen.append(ctx.get(DATABASE))

    runtime.root.use(database(log))
    runtime.root.use(Component("steady", apply=steady, requires=(DATABASE,)))
    await runtime.settled()
    assert seen[0] is seen[1]

async def test_a_fiber_rests_where_its_target_says_after_any_change(runtime: Runtime) -> None:
    log: list[str] = []
    provider = runtime.root.use(database(log))
    consumer = runtime.root.use(reader(log))
    await runtime.settled()
    assert runtime.target(consumer) == consumer.committed
    provider.retire()
    assert runtime.target(consumer) is None
    await runtime.settled()
    assert runtime.quiet()

async def test_transitions_are_recorded_before_they_run_even_when_tasks_start_eagerly(runtime: Runtime) -> None:
    # A loop whose tasks start eagerly, as a running Textual app makes it, would otherwise run a whole transition inside
    # the call recording it, leaving a finished transition recorded as in flight: the fiber could then never leave.
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        log: list[str] = []

        def group(ctx: Context) -> None:
            ctx.use(database(log))
            ctx.use(reader(log))

        fiber = runtime.root.use(Component("group", apply=group))
        await runtime.settled()
        assert log == ["database up", "reader up: hello"]
        await fiber.dispose()
        assert log[2:] == ["reader down: hello", "database down"]
        assert [member.name for member in runtime.fibers()] == []
    finally:
        loop.set_task_factory(previous)
