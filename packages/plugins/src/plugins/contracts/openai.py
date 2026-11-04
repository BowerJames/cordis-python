from typing import Callable

from openai import AsyncOpenAI

from cordis import Key

# A client for an OpenAI-compatible API, given its key and, for anything but OpenAI itself, its base URL.
# Clients are shared: asking again with the same key and URL gives the same client, which keeps its connections pooled.
type OpenAIClients = Callable[[str, str | None], AsyncOpenAI]

OPENAI_CLIENTS: Key[OpenAIClients] = Key("openai_clients")
