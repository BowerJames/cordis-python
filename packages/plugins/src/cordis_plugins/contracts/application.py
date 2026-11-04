import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from cordis import Context, Effect, Fiber, Inverse, Key

# An application is a composition that runs until it is done: until something ends it, until a failure ends it, or
# until nothing holds it open any more. Cordis itself only loads and unloads; this is the run around that, kept by a
# runner (cordis_plugins.components.application keeps one) and offered to the components through APPLICATION. What only the
# runner needs, such as starting the run and stopping what is served, is the runner's own and not part of this.
#
# Promises:
#   when_started callbacks run once everything the composition first loaded has settled, in the order they were
#   registered; a callback registered after that runs at once.
#   hold keeps the application running until its inverse is applied; once started and settled, an application held by
#   nothing ends by itself, so work that should keep it running holds it (serve does so for a task).
#   end asks the application to end with a status; the first status asked for wins. fail records a failure, which ends
#   the application with status 1 unless a containing handler takes it.
#   contain gives a fiber's failures, and those of everything it instantiates, to a handler instead: they fail only
#   what was contained, such as one session, and never the application.
#   serve runs work as a task owned by ctx's fiber, holding the application open while it runs. It is an effect, so
#   unloading the fiber stops the work: its event is set, it is given a grace to finish, and it is cancelled after
#   that. Work that raises fails the application, or whatever contains ctx's fiber. Unless told otherwise, the work
#   waits for the application to start before it runs.
#   When it ends, it does so in two phases: first every served task is told to stop and given its grace, with every
#   fiber still loaded, so work that is finishing (a server draining its connections, a session closing) can still use
#   anything; a task whose fiber is relied upon is stopped only once the tasks of the fibers relying on it have, so a
#   registration with a server ends while the server still accepts. Then the runtime is closed, and every fiber
#   unloaded, dependents before what they depend on.

# Work that runs for a while, such as a server accepting connections: told to stop by the event it is given, and
# expected to return soon after.
type Work = Callable[[asyncio.Event], Awaitable[None]]

class Serve(Protocol):
    def __call__(self, ctx: Context, label: str, work: Work, /, *, after_start: bool = ...) -> Effect: ...

@dataclass(slots=True, frozen=True)
class Application:
    when_started: Callable[[Callable[[], None]], Inverse]
    hold: Callable[[str], Inverse]
    end: Callable[[int], None]
    # Reports a failure where it happened: to the handler containing fiber, if any, or else to the application.
    fail: Callable[[Exception, str, Fiber | None], None]
    contain: Callable[[Fiber, Callable[[Exception, str], None]], Inverse]
    serve: Serve

APPLICATION: Key[Application] = Key("application")

def serve(ctx: Context, label: str, work: Work, *, after_start: bool = True) -> Effect:
    # The application's serve, for a component holding ctx: its component must require APPLICATION.
    return ctx.get(APPLICATION).serve(ctx, label, work, after_start=after_start)
