from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from cordis import Key

from .catalog import Catalog
from .conversation import ToolSpec


@dataclass(slots=True, frozen=True)
class Tool:
    spec: ToolSpec
    # Given the parsed arguments, returns what the model is shown; raising shows the model the error instead.
    run: Callable[[Mapping[str, Any]], Awaitable[str]]

@dataclass(slots=True, frozen=True)
class Workspace:
    # The directory the tools work in, and which paths in it they are kept from. A tool never shows, searches or reads
    # a hidden path, nor anything inside a hidden directory.
    root: Path
    hidden: Callable[[Path], bool]

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

# For the whole application: where the tools work.
WORKSPACE: Key[Workspace] = Key("workspace")
# For one session: the tools on offer in it, what they keep, and a way to tell the agent something.
TOOLS: Key[Catalog[Tool]] = Key("tools")
SESSION_STATE: Key[SessionState] = Key("session_state")
INJECT: Key[Callable[[str], None]] = Key("inject")
