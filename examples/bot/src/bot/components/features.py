from dataclasses import dataclass
from typing import Iterator

from cordis import Component, Context, Inverse, Undo
from cordis_plugins import EVENTS, EventHandler, Topic

from ..contracts import CHANNEL, MESSAGES, STORE, Message


def listen[T](ctx: Context, topic: Topic[T], handler: EventHandler[T]) -> Undo:
    # Subscribes, and gives back the inverse for ctx's fiber to yield, so it listens for as long as the fiber runs.
    # ctx's component must require EVENTS.
    return ctx.get(EVENTS).subscribe(topic, handler)

@dataclass(frozen=True, slots=True)
class GreeterConfig:
    greeting: str = "hello"

def _greeter(ctx: Context, config: GreeterConfig) -> Iterator[Inverse]:
    # The greeting is read as each "hi" is answered, so a new one can be taken in place: nothing the greeter has done
    # depends on the old one, and answering with the new one from here is what a greeter loaded with it would do.
    current = config

    async def on_message(message: Message) -> None:
        if message.text == "hi":
            ctx.get(CHANNEL).say(f"{current.greeting}, {message.user}")

    def accept(new: GreeterConfig) -> bool:
        nonlocal current
        current = new
        return True

    yield listen(ctx, MESSAGES, on_message)
    yield ctx.accept(accept)

# Answers "hi" with its configured greeting. Changing the greeting reloads nothing: the greeter takes it in place.
greeter = Component("greeter", _greeter, config=GreeterConfig, requires=(EVENTS, CHANNEL))

def _counter(ctx: Context) -> Iterator[Inverse]:
    async def on_message(message: Message) -> None:
        count = ctx.get(STORE).increment(message.user)
        if message.text == "count":
            ctx.get(CHANNEL).say(f"{message.user}, that is message {count}")
    yield listen(ctx, MESSAGES, on_message)
    ctx.get(CHANNEL).say("counting from here")
    yield lambda: ctx.get(CHANNEL).say("no longer counting")

# Counts each user's messages and answers "count". It holds nothing itself: the count lives in the store, so
# swapping the store is something the counter follows, not something it has to handle.
counter = Component("counter", _counter, requires=(EVENTS, CHANNEL, STORE))
