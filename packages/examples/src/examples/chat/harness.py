import asyncio
import json
from contextlib import suppress
from typing import Any, Awaitable

from cordis import Component, Context
from cordis_plugins import APPLICATION, serve

from .contracts import (
    AGENT_TYPES,
    CONNECTION,
    INJECT,
    MODEL,
    TOOLS,
    Item,
    Note,
    ToolResult,
    Turn,
    UserMessage,
)

# How much of a tool's output is echoed to the client; the model always gets all of it.
SHOWN = 500

async def _until[T](stopping: asyncio.Event, work: Awaitable[T]) -> T | None:
    # The work's result, or None if told to stop first, in which case the work is given up.
    doing = asyncio.ensure_future(work)
    ending = asyncio.ensure_future(stopping.wait())
    try:
        done, _ = await asyncio.wait({doing, ending}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        ending.cancel()
    if doing in done:
        return doing.result()
    doing.cancel()
    with suppress(asyncio.CancelledError):
        await doing
    return None

def harness() -> Component:
    # The agent loop: takes each message from the connection, and asks the model until it stops calling tools. It runs
    # the agent type the session asked for, with the tools of that type offered in the session, and lets them put notes
    # into the agent's context. The loop is served work of this fiber, so unloading the session ends it.
    def apply(ctx: Context) -> None:
        notes: list[Note] = []
        ctx.provide(INJECT, lambda text: notes.append(Note(text)))
        connection = ctx.get(CONNECTION)

        async def loop(stopping: asyncio.Event) -> None:
            # Everything the session loads has settled once chat says it is ready, so the tools are all on offer.
            if await _until(stopping, connection.ready.wait()) is None and not connection.ready.is_set():
                return
            request = connection.request
            profile = ctx.get(AGENT_TYPES).get(request.agent_type)
            if profile is None:
                raise LookupError(f"no agent type is called {request.agent_type}; "
                                  f"there are {', '.join(ctx.get(AGENT_TYPES).names())}")
            offered = ctx.get(TOOLS)
            unknown = [name for name in profile.tools if offered.get(name) is None]
            if unknown:
                raise LookupError(f"agent type {profile.slug} uses {', '.join(unknown)}, which nothing in the session offers")
            tools = {name: tool for name in profile.tools if (tool := offered.get(name)) is not None}
            socket = connection.socket
            model = ctx.get(MODEL)

            async def send(event: dict[str, Any]) -> None:
                await socket.send(json.dumps(event))

            await send({"type": "ready", "provider": request.provider, "model_name": request.model_name,
                        "agent_type": profile.slug, "tools": list(tools)})
            items: list[Item] = []
            while (text := await _until(stopping, socket.receive())) is not None:
                try:
                    message = json.loads(text)
                    said = message["text"] if isinstance(message, dict) else str(message)
                except (ValueError, KeyError):
                    said = text
                items.append(UserMessage(str(said)))

                for _ in range(profile.max_steps):
                    while notes:
                        note = notes.pop(0)
                        items.append(note)
                        await send({"type": "note", "text": note.text})
                    try:
                        reply = await _until(stopping, model(Turn(profile.instructions, tuple(items),
                                                                  tuple(tool.spec for tool in tools.values()))))
                    except Exception as error:  # noqa: BLE001 - a failed turn is told to the user; the session carries on
                        await send({"type": "error", "message": f"the model failed: {error}"})
                        break
                    if reply is None:
                        return
                    items.append(reply)
                    if reply.text:
                        await send({"type": "assistant", "text": reply.text})
                    if not reply.calls:
                        break
                    for call in reply.calls:
                        await send({"type": "tool_call", "name": call.name, "arguments": call.arguments})
                        tool = tools.get(call.name)
                        try:
                            if tool is None:
                                raise LookupError(f"{call.name} is not one of this agent's tools: {', '.join(tools)}")
                            output = await tool.run(json.loads(call.arguments or "{}"))
                        except Exception as error:  # noqa: BLE001 - the model is shown what went wrong, and can try again
                            output = f"error: {error}"
                        items.append(ToolResult(call.id, output))
                        await send({"type": "tool_result", "name": call.name, "output": output[:SHOWN]})
                else:
                    await send({"type": "error", "message": f"stopped after {profile.max_steps} steps"})
                # However the message's turns ended, the agent is waiting for the next one.
                await send({"type": "done"})

        async def run(stopping: asyncio.Event) -> None:
            try:
                await loop(stopping)
            finally:
                connection.finished.set()

        serve(ctx, "agent loop", run, after_start=False)

    return Component("harness", apply=apply, requires=(APPLICATION, CONNECTION, MODEL, AGENT_TYPES, TOOLS),
                     provides=(INJECT,))
