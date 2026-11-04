from cordis import Component, Context, Inverse
from plugins import EVENTS, EventHandler, Topic

from .contracts import CHANNEL, MESSAGES, STORE, Channel, Message, Store


def listen[T](ctx: Context, topic: Topic[T], handler: EventHandler[T]) -> None:
    # Subscribes for as long as ctx's fiber runs: the subscription is one of its effects. ctx's component must require
    # EVENTS.
    ctx.effect(lambda: ctx.get(EVENTS).subscribe(topic, handler), f"listen to {topic.name}")

class _Transcript(Channel):
    def __init__(self, ctx: Context, lines: list[str]) -> None:
        self._ctx = ctx
        self._lines = lines

    async def receive(self, user: str, text: str) -> None:
        self._lines.append(f"{user}: {text}")
        await self._ctx.get(EVENTS).publish(MESSAGES, Message(user, text))

    def say(self, text: str) -> None:
        self._lines.append(f"bot: {text}")

def transcript_channel(lines: list[str]) -> Component:
    # A channel that writes everything said into lines, in place of a real chat platform.
    def apply(ctx: Context) -> None:
        ctx.provide(CHANNEL, _Transcript(ctx, lines))
    return Component("transcript channel", apply=apply, requires=(EVENTS,), provides=(CHANNEL,))

class _Memory(Store):
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def increment(self, name: str) -> int:
        self._counts[name] = self._counts.get(name, 0) + 1
        return self._counts[name]

def memory_store() -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(STORE, _Memory())
    return Component("memory store", apply=apply, provides=(STORE,))

def greeter(greeting: str = "hello") -> Component:
    # Answers "hi". Its greeting is configuration, bound in here, so changing it is a revision of this entry alone.
    def apply(ctx: Context) -> None:
        async def on_message(message: Message) -> None:
            if message.text == "hi":
                ctx.get(CHANNEL).say(f"{greeting}, {message.user}")
        listen(ctx, MESSAGES, on_message)
    return Component("greeter", apply=apply, requires=(EVENTS, CHANNEL))

def counter() -> Component:
    # Counts each user's messages and answers "count". It holds nothing itself: the count lives in the store, so
    # swapping the store is something the counter follows, not something it has to handle.
    def apply(ctx: Context) -> Inverse:
        async def on_message(message: Message) -> None:
            count = ctx.get(STORE).increment(message.user)
            if message.text == "count":
                ctx.get(CHANNEL).say(f"{message.user}, that is message {count}")
        listen(ctx, MESSAGES, on_message)
        ctx.get(CHANNEL).say("counting from here")
        return lambda: ctx.get(CHANNEL).say("no longer counting")
    return Component("counter", apply=apply, requires=(EVENTS, CHANNEL, STORE))
