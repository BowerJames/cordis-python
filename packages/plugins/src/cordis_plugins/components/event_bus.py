import asyncio
from typing import Any

from cordis import Component, Context, Entries, Inverse

from ..contracts import EVENTS, EventHandler, Events, Topic


def _named(handler: EventHandler[Any]) -> str:
    return getattr(handler, "__qualname__", None) or repr(handler)

class _LocalEvents:
    # Delivers within this process: each topic's handlers kept as entries of their own.
    __slots__ = ("_topics",)

    def __init__(self) -> None:
        self._topics: dict[Topic[Any], Entries[EventHandler[Any]]] = {}

    def subscribe[T](self, topic: Topic[T], handler: EventHandler[T], /) -> Inverse:
        return self._topics.setdefault(topic, Entries()).add(handler)

    async def publish[T](self, topic: Topic[T], event: T, /) -> None:
        called: tuple[EventHandler[T], ...] = tuple(self._topics.get(topic, ()))
        if not called:
            return

        async def deliver(handler: EventHandler[T]) -> None:
            # Awaited in a coroutine of its own, so a handler that raises before it returns an awaitable still fails
            # alone.
            await handler(event)

        results = await asyncio.gather(*(deliver(handler) for handler in called), return_exceptions=True)
        failures: list[Exception] = []
        for handler, result in zip(called, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                result.add_note(f"in handler {_named(handler)} of topic {topic.name}")
                failures.append(result)
        if failures:
            raise ExceptionGroup(f"handlers of {topic.name} failed", failures)

def event_bus() -> Component:
    # Delivers each event to the handlers subscribed to its topic. A subscription is an effect of the subscriber, so
    # a subscriber that unloads stops receiving, and one that loads later starts; there is nothing to declare up front.
    def apply(ctx: Context) -> None:
        events = _LocalEvents()
        ctx.provide(EVENTS, Events(subscribe=events.subscribe, publish=events.publish))
    return Component("event bus", apply=apply, provides=(EVENTS,))
