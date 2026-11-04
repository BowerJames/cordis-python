from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Callable

from cordis import Key

from .catalog import Catalog
from .conversation import Model


@dataclass(slots=True, frozen=True)
class Provider:
    # A source of models, asked for by name when a session opens.
    name: str
    # Connects one session to the model it asked for; the model answers that session's turns until it is closed.
    connect: Callable[[str], AbstractAsyncContextManager[Model]]

@dataclass(slots=True, frozen=True)
class AgentProfile:
    # What an agent type resolves to, by its slug: its instructions, and the names of the tools it may use.
    slug: str
    instructions: str
    tools: tuple[str, ...]
    max_steps: int = 1000

# For the whole application, provided by chat: every provider and agent type a session can ask for.
PROVIDERS: Key[Catalog[Provider]] = Key("providers")
AGENT_TYPES: Key[Catalog[AgentProfile]] = Key("agent_types")
