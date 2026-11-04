from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from cordis import Inverse, Key

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

class Subscribe(Protocol):
    def __call__[T](self, topic: Topic[T], handler: EventHandler[T], /) -> Inverse: ...

class Publish(Protocol):
    def __call__[T](self, topic: Topic[T], event: T, /) -> Awaitable[None]: ...

@dataclass(slots=True, frozen=True)
class Events:
    subscribe: Subscribe
    publish: Publish

EVENTS: Key[Events] = Key("events")
