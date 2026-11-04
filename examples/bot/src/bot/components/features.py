from cordis import Component, Context, Inverse
from cordis_plugins import EVENTS, EventHandler, Topic

from ..contracts import CHANNEL, MESSAGES, STORE, Message


def listen[T](ctx: Context, topic: Topic[T], handler: EventHandler[T]) -> None:
    # Subscribes for as long as ctx's fiber runs: the subscription is one of its effects. ctx's component must require
    # EVENTS.
    ctx.effect(lambda: ctx.get(EVENTS).subscribe(topic, handler), f"listen to {topic.name}")

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
