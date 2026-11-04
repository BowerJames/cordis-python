import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncGenerator, AsyncIterator, Callable, Iterable, Sequence

from openai import omit
from openai.types.chat import ChatCompletionMessageFunctionToolCall, ChatCompletionMessageParam, ChatCompletionToolParam
from openai.types.responses import (
    FunctionToolParam,
    Response,
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseInputItemParam,
)

from cordis import Component, Context, Inverse
from plugins import GET_ENV, OPENAI_CLIENTS, STDERR, GetEnv

from .contracts import (
    CONNECTION,
    MODEL,
    PROVIDERS,
    AssistantMessage,
    Item,
    Model,
    Note,
    Provider,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    UserMessage,
)

OPENAI_API_KEY = "OPENAI_API_KEY"
ZAI_API_KEY = "ZAI_API_KEY"
# Coding Plan keys only work here, not at the general https://api.z.ai/api/paas/v4.
ZAI_CODING_PLAN_URL = "https://api.z.ai/api/coding/paas/v4"

def model() -> Component:
    # Connects the session to the provider and model it asked for, and holds that connection for as long as the session
    # lasts: entering it is this component's effect, and leaving it the inverse.
    async def apply(ctx: Context) -> AsyncIterator[Inverse]:
        request = ctx.get(CONNECTION).request
        provider = ctx.get(PROVIDERS).get(request.provider)
        if provider is None:
            raise LookupError(f"no provider is called {request.provider}; "
                              f"there are {', '.join(sorted(ctx.get(PROVIDERS).names()))}")
        held = AsyncExitStack()
        respond = await held.enter_async_context(provider.connect(request.model_name))
        yield held.aclose
        ctx.provide(MODEL, respond)

    return Component("model", apply=apply, requires=(CONNECTION, PROVIDERS), provides=(MODEL,))

def _key(get_env: GetEnv, variable: str) -> str:
    key = get_env(variable)
    if not key:
        raise LookupError(f"{variable} is not set")
    return key

def _offering(name: str, variable: str, provider: Callable[[Context], Provider]) -> Component:
    # Offers a provider to every session, and says as it loads when the provider has no key: the server still starts,
    # for the providers that do, and sessions asking for this one fail when they open.
    def apply(ctx: Context) -> None:
        write = ctx.find(STDERR)
        if not ctx.get(GET_ENV)(variable) and write is not None:
            write(f"{variable} is not set, so sessions asking for {name} will fail\n")
        offered = provider(ctx)
        ctx.effect(lambda: ctx.get(PROVIDERS).offer(offered), f"offer {name}")
    return Component(name, apply=apply, requires=(PROVIDERS, OPENAI_CLIENTS, GET_ENV))

class _Refused(Exception):
    def __init__(self, code: str | None, message: str) -> None:
        super().__init__(f"{message} ({code})" if code else message)
        self.code = code

def _responses_input(items: Iterable[Item]) -> list[ResponseInputItemParam]:
    converted: list[ResponseInputItemParam] = []
    for item in items:
        match item:
            case UserMessage(text):
                converted.append({"role": "user", "content": text})
            case Note(text):
                converted.append({"role": "developer", "content": text})
            case AssistantMessage(text, calls):
                if text:
                    converted.append({"role": "assistant", "content": text})
                for call in calls:
                    converted.append({"type": "function_call", "call_id": call.id, "name": call.name, "arguments": call.arguments})
            case ToolResult(call_id, output):
                converted.append({"type": "function_call_output", "call_id": call_id, "output": output})
    return converted

def _responses_tools(tools: Sequence[ToolSpec]) -> list[FunctionToolParam]:
    return [{"type": "function", "name": tool.name, "description": tool.description, "parameters": dict(tool.parameters),
             "strict": False} for tool in tools]

def openai_responses() -> Component:
    # Offers OpenAI's Responses API as "openai", over one websocket per session: opened as the session connects to it,
    # closed as the session is released. Each turn sends only what the connection has not seen, continuing from the
    # previous response.

    @asynccontextmanager
    async def connect(ctx: Context, model_name: str) -> AsyncGenerator[Model, None]:
        client = ctx.get(OPENAI_CLIENTS)(_key(ctx.get(GET_ENV), OPENAI_API_KEY), None)
        async with client.responses.connect() as connection:
            previous: str | None = None
            # How many of the conversation's items the connection already holds, the last reply included.
            known = 0
            one_at_a_time = asyncio.Lock()

            async def ask(turn: Turn, items: Sequence[Item], after: str | None) -> Response:
                await connection.response.create(
                    model=model_name, instructions=turn.instructions, input=_responses_input(items),
                    tools=_responses_tools(turn.tools), store=False,
                    previous_response_id=after if after is not None else omit,
                )
                while True:
                    event = await connection.recv()
                    if isinstance(event, ResponseCompletedEvent):
                        return event.response
                    if isinstance(event, ResponseErrorEvent):
                        raise _Refused(event.code, event.message)
                    if isinstance(event, (ResponseFailedEvent, ResponseIncompleteEvent)):
                        error = event.response.error
                        raise _Refused(error.code if error else None, error.message if error else event.type)

            async def respond(turn: Turn) -> AssistantMessage:
                nonlocal previous, known
                async with one_at_a_time:
                    try:
                        response = await ask(turn, turn.items[known:] if previous else turn.items, previous)
                    except _Refused as refused:
                        # The connection keeps only its latest response; without it, the whole conversation is sent again.
                        if refused.code != "previous_response_not_found":
                            raise
                        response = await ask(turn, turn.items, None)
                    previous, known = response.id, len(turn.items) + 1
                calls = tuple(ToolCall(item.call_id, item.name, item.arguments)
                              for item in response.output if isinstance(item, ResponseFunctionToolCall))
                return AssistantMessage(response.output_text, calls)

            yield respond

    return _offering("openai", OPENAI_API_KEY, lambda ctx: Provider("openai", lambda model_name: connect(ctx, model_name)))

def _chat_messages(turn: Turn) -> list[ChatCompletionMessageParam]:
    messages: list[ChatCompletionMessageParam] = [{"role": "system", "content": turn.instructions}]
    for item in turn.items:
        match item:
            case UserMessage(text):
                messages.append({"role": "user", "content": text})
            case Note(text):
                messages.append({"role": "system", "content": text})
            case AssistantMessage(text, calls):
                if calls:
                    messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                        {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                        for call in calls]})
                else:
                    messages.append({"role": "assistant", "content": text})
            case ToolResult(call_id, output):
                messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
    return messages

def _chat_tools(tools: Sequence[ToolSpec]) -> list[ChatCompletionToolParam]:
    return [{"type": "function", "function": {"name": tool.name, "description": tool.description,
                                              "parameters": dict(tool.parameters)}} for tool in tools]

def zai_coding_plan() -> Component:
    # Offers Z.ai's GLM Coding Plan as "zai", through its OpenAI-compatible chat completions API. Each turn is one HTTP
    # request on the application's pooled client, so a session holds no connection of its own: only which model it asked for.

    @asynccontextmanager
    async def connect(ctx: Context, model_name: str) -> AsyncGenerator[Model, None]:
        client = ctx.get(OPENAI_CLIENTS)(_key(ctx.get(GET_ENV), ZAI_API_KEY), ZAI_CODING_PLAN_URL)

        async def respond(turn: Turn) -> AssistantMessage:
            completion = await client.chat.completions.create(
                model=model_name, messages=_chat_messages(turn),
                tools=_chat_tools(turn.tools) if turn.tools else omit,
            )
            message = completion.choices[0].message
            calls = tuple(ToolCall(call.id, call.function.name, call.function.arguments)
                          for call in message.tool_calls or () if isinstance(call, ChatCompletionMessageFunctionToolCall))
            return AssistantMessage(message.content or "", calls)

        yield respond

    return _offering("zai", ZAI_API_KEY, lambda ctx: Provider("zai", lambda model_name: connect(ctx, model_name)))
