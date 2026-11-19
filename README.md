# cordis-python

A Python realization of [_A Programming Paradigm for Spatiotemporal Composability_](https://arxiv.org/abs/2608.25512)
(Shi, Zhang, Cui): components that can be loaded, unloaded and reconfigured while the system runs, with every change
they make reverted when they leave, and every dependency they declare resolved, and followed, as providers come and go.

The paper names two dimensions of dynamic composition, and this kernel supplies both:

- **Temporal composability**, through **revertible effects**: every effect returns its own inverse, the component
  yields each one from its effect function, the runtime accumulates them per fiber, and unloading applies them last in
  first out (§3.1). An inverse a component was given and did not yield is caught at the next seam.
- **Spatial composability**, through **reactive coeffects**: a component declares the keys it requires and provides;
  every change to what is provided is classified against those declarations, activating and deactivating fibers
  (§3.2).

Both are mediated by one context (the _context paradigm_, §3.3), and composed by the fiber calculus of §4, whose nine
rules this runtime implements directly. Where the design here departs from Cordis, the TypeScript implementation, it
does so to follow the paper. Why it is built this way is in [`pattern_language/`](pattern_language):
[One Context](pattern_language/one_context.md), [Every Act Carries Its Undo](pattern_language/revertible_effects.md),
[Wait, Don't Fail](pattern_language/reactive_coeffects.md),
[Every Registration Gets Its Own Ticket](pattern_language/commutative_keys.md),
[Configuration is the Truth](pattern_language/configuration_is_the_truth.md),
[Contracts Are Records](pattern_language/contracts_are_records.md),
[Only Compositions Name Components](pattern_language/only_compositions_name_components.md) and
[Realms, Not Wiring](pattern_language/realms_not_wiring.md).

## Packages

This is a [uv](https://docs.astral.sh/uv/) workspace. The libraries are in `packages/`:

| Package | What it holds |
|---|---|
| [`cordis`](packages/cordis) | The kernel, with no dependencies: keys, effects, contexts, components, fibers and the runtime. |
| [`loader`](packages/loader) | `cordis_loader`: declarative configuration (§5.2.1), a tree of entries reconciled into fibers as it changes, and hot module replacement through those fibers (§5.2.2). |
| [`cordis_plugins`](packages/plugins) | Contracts (keys and the promises behind them) and the components keeping them: an application runner, stdio, the environment, an argparse command line, HTTP clients (httpx, requests) and servers (asyncio, FastAPI), a route table, a health check and a server announcement, an event bus, SQL on SQLite and Postgres, OpenAI clients and timers. |

The examples are in `examples/`, each a project of its own built on those libraries, with its own `pyproject.toml`,
scripts and tests. Each lays out its package the same way: `entrypoints/` holds a module per deliverable, each building
a composition and running it; `components/` the components it composes; and `contracts/` the keys its components meet
on, with the records bound at them. A project whose components keep only `cordis_plugins`' contracts has no
`contracts/` of its own.

| Project | What it is |
|---|---|
| [`echo`](examples/echo) | An echo server: `POST /echo` answers with the body it was sent, beside a health check. |
| [`curl`](examples/curl) | A small curl: an HTTP client on the command line, verbose about what it sends and receives. |
| [`items`](examples/items) | An items API with discovery, keeping its items in memory or in SQLite. |
| [`chat`](examples/chat) | An agent chat server: an agent per websocket session, with tools over a workspace, and a terminal client. |
| [`bot`](examples/bot) | A bot reconfigured while it runs. |

## A first composition

```python
import asyncio
from dataclasses import dataclass
from typing import Callable, Iterator

from cordis import Component, Context, Inverse, Key, open_runtime

GREETING: Key[Callable[[], str]] = Key("greeting")

@dataclass(frozen=True)
class Greeting:
    text: str = "hello"

def _greeter(ctx: Context, config: Greeting) -> Iterator[Inverse]:
    # Provides a key: a revertible effect, whose inverse it yields, withdrawn when the greeter unloads.
    yield ctx.provide(GREETING, lambda: config.text)

# Applied to the configuration its fiber is given (Algorithm 4); the schema builds and checks one from an entry's config.
greeter = Component("greeter", _greeter, config=Greeting, provides=(GREETING,))

def _printer(ctx: Context) -> Iterator[Inverse]:
    # Requires it: activates once it is provided, and reads it through its own teardown.
    print(ctx.get(GREETING)())
    yield lambda: print(f"{ctx.get(GREETING)()}, and goodbye")

# A component with no configuration has no schema, and its effect function takes the context alone.
printer = Component("printer", _printer, requires=(GREETING,))

async def main() -> None:
    async with open_runtime() as runtime:
        runtime.root.use(printer)                           # inserted first, activates second
        greeter_fiber = runtime.root.use(greeter, Greeting())
        await runtime.settled()
        await greeter_fiber.dispose()  # the printer unloads first, while the greeting is still there

asyncio.run(main())
```

```
hello
hello, and goodbye
```

`runtime.describe()` says how the registered fibers fit together without running anything: what keeps any of them
from activating (a key nobody provides, a dependency cycle), the order a load from scratch activates them in, their
states, and who provides what.

Every effect a component performs, it yields the inverse of: `ctx.provide`, `ctx.use` (through the child's `retire`)
and every registry operation (`ROUTES.add`, `EVENTS.subscribe`, `background`) hand one back, so the effect function reads
as the list of everything unloading will undo, in the order it will undo it, and every yield is a seam where activation
can stop. Every `Undo` made while the runtime drives a step is owed by that fiber, whichever operation made it, and one
still unyielded at the next seam fails the activation (`UnyieldedInverse`); the runtime reverts the effect all the same. Where there is nothing to yield to, because the fiber is already active or the caller is an
orchestrator, `ctx.adopt(inverse)` puts it on the accumulator at once; applying the `Undo` it returns reverts that one
effect early.

There is no boot order: the printer waits until the greeting is provided. Disposing the greeter first stops it
providing (`L-Leave`), lets the printer unload while the greeting is still bound, and only then withdraws it
(`L-Unload`, behind its guard).

## From the paper to the code

| Paper | Here |
|---|---|
| Key `k` with value type `𝒱ₖ` | `Key[T]`, compared by identity |
| Effect function `𝔈Γ`, effect iterator `ℑΓ` (Defs. 8, 17) | an `Activation`: an inverse, an awaitable of one, or a sync or async iterator yielding them |
| Accumulator `φ`, `recover` (Defs. 2, 6) | `Accumulator`, applied last in first out |
| `effect` (Def. 12), Algorithm 1 | yielding an inverse from the effect function; `ctx.adopt(inverse)` outside it |
| A witnessed inverse (Def. 8) | `Undo`: an inverse that knows whether it is held, applied at most once; `compose` for several |
| `set(k, v)` (Def. 20) | `ctx.provide(key, value)`: performs the binding, and returns the `Undo` that withdraws it |
| Provider function of metadata (Def. 26) | `ctx.provide_by(key, factory)` |
| `isolate`, `intercept` (Defs. 25, 27) | `ctx.isolate(key, realm)`, `ctx.intercept(key, metadata)`: derived contexts |
| Component `(d, p, e)` (Def. 48) | `Component(name, apply, config=schema, requires, provides)`: a value, the same for every configuration |
| Fiber `⟨d, p, e, π, σ, τ, θ⟩` (Def. 49) | `Fiber`: `component`, `config`, `parent`, `table`, `retired`, `state`, with `committed` as `ω` |
| `use(ctx, component, config)`, `fiber.apply` (Algorithm 4) | `ctx.use(component, config)`; each activation calls `apply(fiber.ctx, fiber.config)` |
| `O-Insert` with instantiation (Def. 52) | `ctx.use(component, config)`, whose inverse is the child's `O-Retire`, `fiber.retire` |
| A revision (§4.4, Configuration) | `fiber.revise(component, config)`: retire, remove, reinsert where the fiber stood |
| `config` handed to the component (§5.2.1) | `fiber.update(config)`: taken in place when the fiber's acceptors (`ctx.accept`) take it, a revision otherwise |
| `O-Retire`, `O-Remove` | `fiber.retire()`, the same `Undo`; removal is taken as soon as its premises hold |
| `L-Begin`, `L-Iter`, `L-Finish`, `L-Divert`, `L-Leave`, `L-Unload` | `Runtime.refresh` and `Runtime._drive` |
| Algorithm 6, proxy access | `ctx.get(key)`, checked against the declaring fiber's committed view |
| An optional dependency, as a nested fiber | `ctx.while_provided(keys, apply)`: a child whose `d` is keys, so it comes and goes with them |
| The context paradigm (§3.3) | `Context` offers only the operations above; an orchestrator uses `Fiber` handles, `runtime.lookup(key)` and `cordis.orchestration` |
| A commutative, witnessed coeffect (Def. 46) | `Entries`: each addition an entry of its own |

## Configuration, and running the examples

The loader keeps a tree of entries and the fibers realizing it in step. An entry names a component (Definition 81's
url), and its `config` is what the component's schema builds the fiber's configuration from, checked as the entry
loads, so a bad one fails that entry's fiber alone:

```python
from cordis_loader import Entry, Loader

loader = Loader(runtime.root)
await loader.load([
    Entry("events", "cordis_plugins:event_bus"),
    Entry("features", children=[Entry("greeter", "bot.components:greeter", config={"greeting": "hey"})]),
    Entry("tenant", children=[...], isolate={STORE: True}),
])
print(loader.describe())          # how it fits together, building each configuration but running nothing
await loader.load(changed)        # reconciles by id: only what the change reaches reloads
await loader.reload(["bot.components.features"], source_imports(["bot"]))  # hot module replacement
```

A change to an entry's config alone is handed to its fiber (`fiber.update`), and the component decides what it means
(§5.2.1). By default it is a revision, so the fiber's dependents reactivate against the new one. A component that can
take some changes in place says so with `ctx.accept`, yielding the undo as it does every effect's:

```python
def _greeter(ctx: Context, config: GreeterConfig) -> Iterator[Inverse]:
    current = config

    async def on_message(message: Message) -> None:
        if message.text == "hi":
            ctx.get(CHANNEL).say(f"{current.greeting}, {message.user}")

    def accept(new: GreeterConfig) -> bool:
        nonlocal current
        current = new          # nothing done so far depended on the old greeting
        return True

    yield listen(ctx, MESSAGES, on_message)
    yield ctx.accept(accept)
```

An acceptor answers to the same endpoint as the revision it saves (Theorem 80): it accepts only when the fiber now runs
as one activated with the new configuration would, and the fiber keeps the configuration it took, so a later
reactivation applies it. Chat accepts a new session template that way: each live session's loader is reconciled to it,
the keyed diff a group applies, so a tool added to the template reaches every open session without a reconnect, while
a new path, which is a new route, declines and is revised.

```sh
uv sync

uv run echo-server                    # an echo server; ECHO_SERVER=fastapi serves it with FastAPI instead
uv run curl-example http://127.0.0.1:8000/echo -d hello -v
uv run site-example --port 8080       # an items API; SITE_DATABASE=site.sqlite keeps items in SQLite
uv run site-example --routes          # the route table, without binding a port
uv run chat-server                    # an agent per websocket session
uv run chat-server --describe         # how it fits together, application and a session, without starting it
uv run chat-tui                       # the same server, with a terminal client of its own
uv run bot-example                    # a conversation, with the configuration changed twice while it runs
```

The [justfile](justfile) runs the same entrypoints by name, passing on any arguments: `just echo-server`, `just curl`,
`just items-server`, `just chat-server`, `just chat-tui` and `just bot`; `just` alone lists them.

The chat server reads `ZAI_API_KEY` and `OPENAI_API_KEY` from the environment, and its tools look in
`WORKSPACE_ROOT`, or the current directory, never at what git ignores there. `just chat-server` and `just chat-tui` load
`examples/chat/.env` into its environment first, when there is one; a variable already set in the shell keeps its value.

## The microkernel's examples, in this framework's style

The four example programs of the `microkernel` project are rebuilt here, as the `echo`, `curl`, `items` and `chat` projects, behaving the same and tested by the same
tests, ported. What changes is how they are put together, since this framework composes by effects and coeffects that
can be changed while the application runs, where the microkernel plans a fixed boot:

| microkernel | here |
|---|---|
| `kernel.register(...)`, `kernel.replace(TOKEN, plugin)` | a list of `Entry`s with stable ids; `patched(entries, Entry(id, ...))` swaps one, at runtime too through `Loader.load` |
| `kernel.describe()` | `describe(entries)`: the order a load from scratch activates them in, and why, without running anything |
| a capability introduced by one plugin | a key provided by one component |
| a slot, contributed before boot and collected | a key whose value is a table, each addition its own entry, whose `Undo` its contributor yields (`RouteTable`, `Catalog`, `Arguments`, `Events`) |
| adapting a capability (`verbose` wrapping the HTTP requester) | observing it: `HTTP.observe` is a registration of its own, so it needs no place in any order |
| `server_cli` adapting the environment for the server | `server_cli` instantiating the server once the command line is parsed, with host and port as its configuration |
| services, `END`, `STARTED`, `kernel.run()` | `run_application(entries)`: a server binds and accepts as it activates and yields what closes it; `background(ctx, label, work)` runs a fiber's work, failing the fiber if it raises; `APPLICATION.end`, and `when_started` for emissions once the first load has quiesced; ending closes the runtime, which unloads dependents first |
| a scope opened per websocket connection | a session group whose session keys are isolated into realms of its own; its failures are contained to it |

Some behaviour comes from the framework rather than from the examples: routes live in their own table, so a server
can be swapped while running without any route noticing, and a route withdrawn is answered 404 from the next request;
two servers are kept apart by isolating their keys, with nothing to wire; discovery deregisters while the server still
accepts because it relies on the server's address, not because anything orders it.

## Tests and checks

```sh
uv sync
uv run pytest                 # internal tests only
uv run pytest -m external     # the external ones: real sockets, real servers, mypy
uv run pytest -m ""           # everything
uv run mypy                   # strict, over sources and tests
uv run ruff check
```

The tests are organised by what the paper proves: `test_effect.py` (§3.1), `test_coeffects.py` (§3.2 and the
access discipline of Algorithm 6), `test_lifecycle.py` (the rules of §4.2 and Theorems 70–71), `test_recovery.py`
(Theorems 43 and 68), `test_failures.py` and `test_isolation.py` (§4.4), `test_instantiation.py` (Def. 52), and
`test_metatheory.py` (Theorems 73 and 80, and cycles from §6.5), `test_describe.py` and `test_revision.py`. The
loader's tests reconcile configurations against loading them from scratch (Theorem 80 again), and its HMR tests
replace real modules written to a temporary package, including a failed import that rolls back.

## Where this departs from Cordis

- A fiber whose own work fails once it is active takes the route the paper gives an activation that raises (§4.4):
  it leaves, unloads, and rests failed (`ctx.fail`). The paper defines failure for iterations alone.
- An application runs until something ends it, and ending it closes the runtime, nothing more: the guard on L-Unload
  orders the teardown. A route's withdrawal waits for the requests it is still answering (§6.2), so a contributor
  finishes what it took on while still loaded. `when_started` marks the first load's quiescence (Definition 53), a
  property of the whole state that no rule reads, so it is kept for emissions, and for the command line, which can
  only be read once every component has registered its arguments.

- There is no `ctx.effect`: a component yields every inverse itself, and an operation performed for it (`provide`,
  `use`, a registration) gives the inverse back rather than taking it. What it does not yield or adopt is caught: at the
  next seam during activation, as the fiber unloads otherwise, and as a `DroppedInverse` warning for an `Undo` that
  nobody ever held.
- Inverses run one at a time, newest first, as Theorem 16 has them; Cordis starts asynchronous disposers together.
- A second provider of a key in a realm is refused when it is inserted (O-Insert's premise), not when it provides.
- A failed fiber is not retried until it is revised (§4.4); `fiber.revise()` is that revision, as is
  `fiber.update(config)` on a failed fiber.
- A fiber resting inactive takes a new configuration as it stands, rather than by the composite revision §4.4 gives
  every change of configuration: it has installed nothing and nothing has committed to it, so the endpoint is the same.
- Hot module replacement invalidates only the modules on a way from an entry to a change, rather than the entry's
  whole import tree: a key's identity is the object its module made, and Python cannot re-make it equal.

The Postgres tests need a server they may do anything to. They use the one `TEST_POSTGRES_DSN` names, or start a
throwaway `postgres:17-alpine` in Docker, and skip when there is neither.
