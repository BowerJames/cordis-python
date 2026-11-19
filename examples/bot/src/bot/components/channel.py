from dataclasses import dataclass

from cordis import Component, Context, Inverse
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

@dataclass(frozen=True, slots=True)
class TranscriptConfig:
    # Where everything said is written.
    lines: list[str]

def _transcript_channel(ctx: Context, config: TranscriptConfig) -> Inverse:
    transcript = _Transcript(ctx, config.lines)
    return ctx.provide(CHANNEL, Channel(receive=transcript.receive, say=transcript.say))

# A channel that writes everything said into the configured lines, in place of a real chat platform.
transcript_channel = Component("transcript channel", _transcript_channel, config=TranscriptConfig, requires=(EVENTS,),
                               provides=(CHANNEL,))
