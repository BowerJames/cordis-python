from dataclasses import dataclass

from cordis import Key
from plugins import Topic

# The keys and topics the bot's components meet on. They live in a module of their own, which changes rarely, so
# reloading any component leaves every key with the identity the running components already resolved.

@dataclass(slots=True, frozen=True)
class Message:
    user: str
    text: str

MESSAGES: Topic[Message] = Topic("messages")

class Channel:
    # Where the bot talks: receive is called by whatever delivers messages, say by whatever answers them.
    async def receive(self, user: str, text: str) -> None:
        raise NotImplementedError

    def say(self, text: str) -> None:
        raise NotImplementedError

CHANNEL: Key[Channel] = Key("channel")

class Store:
    # A counter per name, kept wherever the provider keeps it.
    def increment(self, name: str) -> int:
        raise NotImplementedError

STORE: Key[Store] = Key("store")
