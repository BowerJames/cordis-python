from typing import Any, Callable, Generic, TypeVar

from cordis import Component, Context, Entries, Inverse, Key

T = TypeVar("T")

type Handler[P] = Callable[[P], None]

class Topic(Generic[T]):
    # A named kind of event and the payload it carries. Its identity is the object, like a key's.
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"Topic({self.name!r})"

class Events:
    # The value bound at EVENTS. Its one mutating operation, on, is commutative by construction: every subscription
    # is an entry of its own, so two subscriptions, even of the same handler, withdraw independently and in either
    # order (Definition 46). Emitting reads the subscriptions and changes none of them.
    __slots__ = ("_topics",)

    def __init__(self) -> None:
        self._topics: dict[Topic[Any], Entries[Callable[[Any], None]]] = {}

    def on(self, topic: Topic[T], handler: Handler[T]) -> Inverse:
        return self._topics.setdefault(topic, Entries()).add(handler)

    def emit(self, topic: Topic[T], payload: T) -> int:
        # Calls each handler subscribed when the emit began, in the order they subscribed; returns how many.
        handlers = list(self._topics.get(topic, ()))
        for handler in handlers:
            handler(payload)
        return len(handlers)

EVENTS: Key[Events] = Key("events")

def event_bus() -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(EVENTS, Events())
    return Component("event bus", apply=apply, provides=(EVENTS,))

def listen[P](ctx: Context, topic: Topic[P], handler: Handler[P]) -> None:
    # Subscribes for as long as ctx's fiber runs: the subscription is an effect of that fiber, withdrawn when it
    # unloads. ctx's component must require EVENTS.
    ctx.effect(lambda: ctx.get(EVENTS).on(topic, handler), f"listen to {topic.name}")
