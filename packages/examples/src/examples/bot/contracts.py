from dataclasses import dataclass
from typing import Awaitable, Callable

from cordis import Key
from cordis_plugins import Topic

# The keys and topics the bot's components meet on. They live in a module of their own, which changes rarely, so
# reloading any component leaves every key with the identity the running components already resolved.

@dataclass(slots=True, frozen=True)
class Message:
    user: str
    text: str

MESSAGES: Topic[Message] = Topic("messages")

@dataclass(slots=True, frozen=True)
class Channel:
    # Where the bot talks: receive is called by whatever delivers messages, with the user and their text; say by
    # whatever answers them.
    receive: Callable[[str, str], Awaitable[None]]
    say: Callable[[str], None]

CHANNEL: Key[Channel] = Key("channel")

@dataclass(slots=True, frozen=True)
class Store:
    # A counter per name, kept wherever the provider keeps it: increment counts one more, and gives the count.
    increment: Callable[[str], int]

STORE: Key[Store] = Key("store")
