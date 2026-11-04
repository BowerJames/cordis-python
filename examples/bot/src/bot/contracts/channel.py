from dataclasses import dataclass
from typing import Awaitable, Callable

from cordis import Key
from cordis_plugins import Topic


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
