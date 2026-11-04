from cordis import Component, Context

from .contracts import AGENT_TYPES, AgentProfile


def _agent_type(profile: AgentProfile) -> Component:
    # Offers one agent type to every session: it is only data, so offering it is its one effect.
    def apply(ctx: Context) -> None:
        ctx.effect(lambda: ctx.get(AGENT_TYPES).offer(profile), f"offer {profile.slug}")
    return Component(profile.slug, apply=apply, requires=(AGENT_TYPES,))

def explorer() -> Component:
    return _agent_type(AgentProfile(
        slug="explorer",
        instructions=("You explore a codebase to answer questions about it. List directories, search with grep and "
                      "read files before answering; cite the files you used. Paths are relative to the workspace."),
        tools=("listDir", "grep", "read"),
    ))

def reader() -> Component:
    return _agent_type(AgentProfile(
        slug="reader",
        instructions=("You answer questions by reading files the user points you to. You cannot search; list "
                      "directories to find your way. Paths are relative to the workspace."),
        tools=("listDir", "read"),
    ))
