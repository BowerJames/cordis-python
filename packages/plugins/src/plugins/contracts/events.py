import asyncio
from typing import Any, Awaitable, Callable

from cordis import Entries, Inverse, Key

# Every event bus keeps these promises: publish calls every handler subscribed to the topic, concurrently, and returns
# once they have all returned; a topic with no handlers returns at once. A handler that raises never stops the others:
# once they have all finished, publish raises an ExceptionGroup of every failure, each noted with its topic and
# handler. Cancelling publish cancels the handlers still running. A subscription is an entry of its own, withdrawn by
# the inverse subscribe returns, so subscribers come and go independently, even when they subscribe the same handler.

# Called with each event published to the topic it was subscribed to.
type EventHandler[T] = Callable[[T], Awaitable[None]]

class Topic[T]:
    # A kind of event, typed by the event it carries. Its name is for people; its identity is the object.
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"Topic({self.name!r})"

def _named(handler: EventHandler[Any]) -> str:
    return getattr(handler, "__qualname__", None) or repr(handler)

class Events:
    __slots__ = ("_topics",)

    def __init__(self) -> None:
        self._topics: dict[Topic[Any], Entries[EventHandler[Any]]] = {}

    def subscribe[T](self, topic: Topic[T], handler: EventHandler[T]) -> Inverse:
        return self._topics.setdefault(topic, Entries()).add(handler)

    async def publish[T](self, topic: Topic[T], event: T) -> None:
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

EVENTS: Key[Events] = Key("events")
