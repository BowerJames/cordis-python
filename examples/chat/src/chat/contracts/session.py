import asyncio
from dataclasses import dataclass, field
from typing import Any

from cordis import Key
from cordis_plugins import Socket, Topic

from .conversation import MODEL
from .tools import INJECT, SESSION_STATE, TOOLS


# What a session is opened with: the query of /ws/chat.
@dataclass(slots=True, frozen=True)
class SessionRequest:
    provider: str
    model_name: str
    agent_type: str

@dataclass(slots=True)
class Connection:
    # One session, as the components loaded in it see it: the socket, what was asked for, and how it is going.
    socket: Socket
    request: SessionRequest
    number: int
    # Set by chat once everything the session loads has settled, so the agent loop knows what it has.
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    # Set when the session is over: the client has gone, or something in it failed.
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    failures: list[str] = field(default_factory=list)

# What chat publishes about the sessions it opens, for whoever wants to know: each opened is followed by one closed.
@dataclass(slots=True, frozen=True)
class SessionOpened:
    # Numbered in the order sessions open, so that each closing can be matched to its opening.
    session: int
    request: SessionRequest

@dataclass(slots=True, frozen=True)
class SessionClosed:
    session: int
    request: SessionRequest
    # Each failure, with where it happened; empty when the session ended without failing.
    failures: tuple[str, ...] = ()

SESSION_OPENED: Topic[SessionOpened] = Topic("session_opened")
SESSION_CLOSED: Topic[SessionClosed] = Topic("session_closed")

CONNECTION: Key[Connection] = Key("connection")
# Every key a session binds for itself, each isolated into a realm of the session's own, so every session binds them
# independently.
SESSION_KEYS: tuple[Key[Any], ...] = (CONNECTION, MODEL, SESSION_STATE, INJECT, TOOLS)
