from cordis import Component, Context
from cordis_plugins import EVENTS

from ..contracts import CHANNEL, MESSAGES, Channel, Message


class _Transcript:
    def __init__(self, ctx: Context, lines: list[str]) -> None:
        self._ctx = ctx
        self._lines = lines

    async def receive(self, user: str, text: str) -> None:
        self._lines.append(f"{user}: {text}")
        await self._ctx.get(EVENTS).publish(MESSAGES, Message(user, text))

    def say(self, text: str) -> None:
        self._lines.append(f"bot: {text}")

def transcript_channel(lines: list[str]) -> Component:
    # A channel that writes everything said into lines, in place of a real chat platform.
    def apply(ctx: Context) -> None:
        transcript = _Transcript(ctx, lines)
        ctx.provide(CHANNEL, Channel(receive=transcript.receive, say=transcript.say))
    return Component("transcript channel", apply=apply, requires=(EVENTS,), provides=(CHANNEL,))
