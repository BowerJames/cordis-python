from cordis import Component, Context, Inverse

from ..contracts import AGENT_TYPES, AgentProfile


def _agent_type(profile: AgentProfile) -> Component[None]:
    # Offers one agent type to every session: it is only data, so offering it is its one effect.
    def apply(ctx: Context) -> Inverse:
        return ctx.get(AGENT_TYPES).offer(profile)
    return Component(profile.slug, apply, requires=(AGENT_TYPES,))

explorer = _agent_type(AgentProfile(
    slug="explorer",
    instructions=("You explore a codebase to answer questions about it. List directories, search with grep and "
                  "read files before answering; cite the files you used. Paths are relative to the workspace."),
    tools=("listDir", "grep", "read"),
))

reader = _agent_type(AgentProfile(
    slug="reader",
    instructions=("You answer questions by reading files the user points you to. You cannot search; list "
                  "directories to find your way. Paths are relative to the workspace."),
    tools=("listDir", "read"),
))
