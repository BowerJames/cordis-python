import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from cordis import Inverse, Key
from plugins import Socket, Topic


# What a session is opened with: the query of /ws/chat.
@dataclass(slots=True, frozen=True)
class SessionRequest:
    provider: str
    model_name: str
    agent_type: str

# The conversation, as every provider sees it; each translates it into its own API's terms.
@dataclass(slots=True, frozen=True)
class UserMessage:
    text: str

@dataclass(slots=True, frozen=True)
class ToolCall:
    id: str
    name: str
    # JSON, exactly as the model wrote it.
    arguments: str

@dataclass(slots=True, frozen=True)
class AssistantMessage:
    text: str
    calls: tuple[ToolCall, ...] = ()

@dataclass(slots=True, frozen=True)
class ToolResult:
    call_id: str
    output: str

@dataclass(slots=True, frozen=True)
class Note:
    # Put into the agent's context by something other than the user, such as a tool.
    text: str

type Item = UserMessage | AssistantMessage | ToolResult | Note

@dataclass(slots=True, frozen=True)
class ToolSpec:
    name: str
    description: str
    # A JSON schema for the arguments.
    parameters: Mapping[str, Any]

@dataclass(slots=True, frozen=True)
class Turn:
    # Everything a model is asked with: the whole conversation so far, every time.
    instructions: str
    items: tuple[Item, ...]
    tools: tuple[ToolSpec, ...]

# Answers one turn; a provider may keep what it needs between turns, such as a connection or the last response's id.
type Model = Callable[[Turn], Awaitable[AssistantMessage]]

@dataclass(slots=True, frozen=True)
class Provider:
    # A source of models, asked for by name when a session opens.
    name: str
    # Connects one session to the model it asked for; the model answers that session's turns until it is closed.
    connect: Callable[[str], AbstractAsyncContextManager[Model]]

@dataclass(slots=True, frozen=True)
class Tool:
    spec: ToolSpec
    # Given the parsed arguments, returns what the model is shown; raising shows the model the error instead.
    run: Callable[[Mapping[str, Any]], Awaitable[str]]

@dataclass(slots=True, frozen=True)
class AgentProfile:
    # What an agent type resolves to, by its slug: its instructions, and the names of the tools it may use.
    slug: str
    instructions: str
    tools: tuple[str, ...]
    max_steps: int = 20

@dataclass(slots=True, frozen=True)
class Catalog[T]:
    # Things on offer, each by a name: providers, agent types, a session's tools. Offering is an entry of its own,
    # withdrawn by the inverse offer returns, so whatever offers comes and goes without disturbing the rest; offering
    # a name already on offer raises ValueError.
    offer: Callable[[T], Inverse]
    # Every name on offer now, in the order they were offered.
    names: Callable[[], tuple[str, ...]]
    # What is on offer by a name, or None.
    get: Callable[[str], T | None]

@dataclass(slots=True, frozen=True, eq=False)
class StateKey[V]:
    # One piece of session state; compared by identity, so each tool's keys are its own.
    name: str
    initial: Callable[[], V]

class ReadState(Protocol):
    # The value kept at key, which starts as key.initial() the first time it is read.
    def __call__[V](self, key: StateKey[V], /) -> V: ...

class WriteState(Protocol):
    def __call__[V](self, key: StateKey[V], value: V, /) -> None: ...

@dataclass(slots=True, frozen=True)
class SessionState:
    # What tools keep between executions, for as long as the session lasts.
    get: ReadState
    set: WriteState

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

# For the whole application, provided by chat: every provider and agent type a session can ask for.
PROVIDERS: Key[Catalog[Provider]] = Key("providers")
AGENT_TYPES: Key[Catalog[AgentProfile]] = Key("agent_types")

@dataclass(slots=True, frozen=True)
class Workspace:
    # The directory the tools work in, and which paths in it they are kept from. A tool never shows, searches or reads
    # a hidden path, nor anything inside a hidden directory.
    root: Path
    hidden: Callable[[Path], bool]

WORKSPACE: Key[Workspace] = Key("workspace")

# For one session: each isolated into a realm of the session's own, so every session binds them independently.
CONNECTION: Key[Connection] = Key("connection")
MODEL: Key[Model] = Key("model")
SESSION_STATE: Key[SessionState] = Key("session_state")
INJECT: Key[Callable[[str], None]] = Key("inject")
TOOLS: Key[Catalog[Tool]] = Key("tools")
SESSION_KEYS: tuple[Key[Any], ...] = (CONNECTION, MODEL, SESSION_STATE, INJECT, TOOLS)
