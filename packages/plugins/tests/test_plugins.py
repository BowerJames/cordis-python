import asyncio

from cordis import Component, Context, Runtime
from plugins import EVENTS, TIMER, Events, Topic, event_bus, listen, timer

PINGS: Topic[str] = Topic("pings")

def subscriber(name: str, heard: list[str]) -> Component:
    def apply(ctx: Context) -> None:
        listen(ctx, PINGS, lambda payload: heard.append(f"{name} heard {payload}"))
    return Component(name, apply=apply, requires=(EVENTS,))


def test_two_subscriptions_of_one_handler_withdraw_independently() -> None:
    # The event bus's key is commutative: each subscription is an entry of its own (Definition 46).
    events = Events()
    heard: list[str] = []
    first = events.on(PINGS, heard.append)
    events.on(PINGS, heard.append)
    assert events.emit(PINGS, "a") == 2
    first()
    assert events.emit(PINGS, "b") == 1
    assert heard == ["a", "a", "b"]

def test_topics_with_the_same_name_are_different_topics() -> None:
    events = Events()
    heard: list[str] = []
    events.on(Topic[str]("pings"), heard.append)
    assert events.emit(PINGS, "a") == 0

async def test_a_subscription_lasts_as_long_as_its_fiber(runtime: Runtime) -> None:
    heard: list[str] = []
    runtime.root.use(event_bus())
    a = runtime.root.use(subscriber("a", heard))
    runtime.root.use(subscriber("b", heard))
    await runtime.settled()
    bus = runtime.root.find(EVENTS)
    assert bus is not None
    bus.emit(PINGS, "one")
    await a.dispose()
    bus.emit(PINGS, "two")
    assert heard == ["a heard one", "b heard one", "b heard two"]

async def test_subscribers_unload_before_the_bus_they_subscribed_to(runtime: Runtime) -> None:
    heard: list[str] = []
    bus = runtime.root.use(event_bus())
    runtime.root.use(subscriber("a", heard))
    await runtime.settled()
    events = runtime.root.find(EVENTS)
    await bus.dispose()
    assert events is not None and events.emit(PINGS, "late") == 0

async def test_a_timer_set_as_an_effect_never_fires_after_its_fiber_unloads(runtime: Runtime) -> None:
    fired: list[str] = []

    def apply(ctx: Context) -> None:
        ctx.effect(lambda: ctx.get(TIMER).after(0.01, lambda: fired.append("late")))
        ctx.effect(lambda: ctx.get(TIMER).after(0, lambda: fired.append("soon")))

    runtime.root.use(timer())
    fiber = runtime.root.use(Component("waiter", apply=apply, requires=(TIMER,)))
    await runtime.settled()
    await asyncio.sleep(0.001)
    await fiber.dispose()
    await asyncio.sleep(0.02)
    assert fired == ["soon"]

async def test_an_interval_repeats_until_cancelled(runtime: Runtime) -> None:
    ticks: list[int] = []
    runtime.root.use(timer())
    await runtime.settled()
    timers = runtime.root.find(TIMER)
    assert timers is not None
    cancel = timers.every(0.001, lambda: ticks.append(len(ticks)))
    while len(ticks) < 3:
        await asyncio.sleep(0.001)
    cancel()
    count = len(ticks)
    await asyncio.sleep(0.01)
    assert len(ticks) == count
    assert len(timers) == 0
