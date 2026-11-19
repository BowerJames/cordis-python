from typing import AsyncIterator

from openai import AsyncOpenAI

from cordis import Component, Context, Inverse

from ..contracts import OPENAI_CLIENTS


async def _openai_sdk(ctx: Context) -> AsyncIterator[Inverse]:
    clients: dict[tuple[str, str | None], AsyncOpenAI] = {}

    async def close() -> None:
        for made in clients.values():
            await made.close()

    yield close

    def client(api_key: str, base_url: str | None) -> AsyncOpenAI:
        if (api_key, base_url) not in clients:
            clients[(api_key, base_url)] = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return clients[(api_key, base_url)]

    yield ctx.provide(OPENAI_CLIENTS, client)

# Clients for OpenAI and any provider with an OpenAI-compatible API. It knows no keys or URLs of its own: whoever asks
# for a client says which. Clients are shared, and closed as the component unloads.
openai_sdk = Component("openai sdk", _openai_sdk, provides=(OPENAI_CLIENTS,))
