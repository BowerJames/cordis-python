from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from cordis import Key

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

# For one session: the model it asked for, connected.
MODEL: Key[Model] = Key("model")
