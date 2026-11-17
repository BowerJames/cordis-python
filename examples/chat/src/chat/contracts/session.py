import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from cordis import Key
from cordis_plugins import Socket

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

# What chat tells a session log about the sessions it opens: each opened is followed by one closed.
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

@dataclass(slots=True, frozen=True)
class SessionLog:
    # Told of each session as it opens, and again as it closes. A session tells it through a fiber of its own that
    # requires this key, so the log is what the session depends on, and by the guard on L-Unload it cannot leave
    # before every session it was told of has said it closed, even as the whole application ends (Theorem 70). An
    # event bus could not promise that: a publisher and a subscriber are both its dependents, which nothing orders.
    opened: Callable[[SessionOpened], None]
    closed: Callable[[SessionClosed], None]

SESSION_LOG: Key[SessionLog] = Key("session_log")

CONNECTION: Key[Connection] = Key("connection")
# Every key a session binds for itself, each isolated into a realm of the session's own, so every session binds them
# independently.
SESSION_KEYS: tuple[Key[Any], ...] = (CONNECTION, MODEL, SESSION_STATE, INJECT, TOOLS)
