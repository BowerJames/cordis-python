from dataclasses import dataclass
from typing import Awaitable, Callable

from cordis import Fiber, Key, Undo

# An application is a composition that runs until something ends it: a component asking it to, a signal, or a failure.
# Cordis itself only loads and unloads; this is the run around that, kept by a runner (cordis_plugins.components.
# application keeps one) and offered to the components through APPLICATION. Ending it closes the runtime, which
# unloads every fiber with dependents before what they depend on (the guard on L-Unload), so whatever is finishing can
# still use what it relied on.
#
# Promises:
#   The application has started once everything the composition first loaded has quiesced (Definition 53, Theorem 73).
#   That is a property of the whole state and no premise of any rule, so it is for emissions alone, such as saying
#   where a server listens, or a command line read once everything has registered its arguments: when_started
#   callbacks run then, in the order they were registered, and one registered after that runs at once, returning the
#   Undo its caller yields; started() resolves then to True, or to False if the application ended first.
#   end asks the application to end with a status; the first status asked for wins. fail records a failure, which ends
#   the application with status 1 unless a containing handler takes it.
#   contain gives a fiber's failures, and those of everything it instantiates, to a handler instead: they fail only
#   what was contained, such as one session, and never the application.

@dataclass(slots=True, frozen=True)
class Application:
    when_started: Callable[[Callable[[], None]], Undo]
    started: Callable[[], Awaitable[bool]]
    end: Callable[[int], None]
    # Reports a failure where it happened: to the handler containing fiber, if any, or else to the application.
    fail: Callable[[Exception, str, Fiber | None], None]
    contain: Callable[[Fiber, Callable[[Exception, str], None]], Undo]

APPLICATION: Key[Application] = Key("application")
