# cordis-python

A Python realization of [_A Programming Paradigm for Spatiotemporal Composability_](https://arxiv.org/abs/2608.25512)
(Shi, Zhang, Cui): components that can be loaded, unloaded and reconfigured while the system runs, with every change
they make reverted when they leave, and every dependency they declare resolved, and followed, as providers come and go.

The paper names two dimensions of dynamic composition, and this kernel supplies both:

- **Temporal composability**, through **revertible effects**: every effect returns its own inverse, the runtime
  accumulates the inverses of each fiber, and unloading applies them last in first out (§3.1).
- **Spatial composability**, through **reactive coeffects**: a component declares the keys it requires and provides;
  every change to what is provided is classified against those declarations, activating and deactivating fibers
  (§3.2).

Both are mediated by one context (the _context paradigm_, §3.3), and composed by the fiber calculus of §4, whose nine
rules this runtime implements directly. Where the design here departs from Cordis, the TypeScript implementation, it
does so to follow the paper. Why it is built this way is in [`pattern_language/`](pattern_language):
[One Context](pattern_language/one_context.md), [Every Act Carries Its Undo](pattern_language/revertible_effects.md),
[Wait, Don't Fail](pattern_language/reactive_coeffects.md),
[Every Registration Gets Its Own Ticket](pattern_language/commutative_keys.md) and
[Configuration is the Truth](pattern_language/configuration_is_the_truth.md).

## Packages

This is a [uv](https://docs.astral.sh/uv/) workspace:

| Package | What it holds |
|---|---|
| [`cordis`](packages/cordis) | The kernel, with no dependencies: keys, effects, contexts, components, fibers and the runtime. |
| [`loader`](packages/loader) | `cordis_loader`: declarative configuration (§5.2.1), a tree of entries reconciled into fibers as it changes, and hot module replacement through those fibers (§5.2.2). |
| [`plugins`](packages/plugins) | Contracts (keys and the promises behind them) and the components keeping them: an application runner, stdio, the environment, an argparse command line, HTTP clients (httpx, requests) and servers (asyncio, FastAPI), a route table, an event bus, SQL on SQLite and Postgres, OpenAI clients and timers. |
| [`examples`](packages/examples) | Applications composed from them, each started from a file in `entrypoints/`: the microkernel project's echo server, curl, items site and agent chat server, rebuilt in the style of this framework, and a bot reconfigured while it runs. |

## A first composition

```python
import asyncio
from typing import Callable, Iterator

from cordis import Component, Context, Inverse, Key, open_runtime

GREETING: Key[Callable[[], str]] = Key("greeting")

def greeter() -> Component:
    # Provides a key: a revertible effect, withdrawn when the greeter unloads.
    def apply(ctx: Context) -> None:
        ctx.provide(GREETING, lambda: "hello")
    return Component("greeter", apply=apply, provides=(GREETING,))

def printer() -> Component:
    # Requires it: activates once it is provided, and reads it through its own teardown.
    def apply(ctx: Context) -> Iterator[Inverse]:
        print(ctx.get(GREETING)())
        yield lambda: print(f"{ctx.get(GREETING)()}, and goodbye")
    return Component("printer", apply=apply, requires=(GREETING,))

async def main() -> None:
    async with open_runtime() as runtime:
        runtime.root.use(printer())   # inserted first, activates second
        greeter_fiber = runtime.root.use(greeter())
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

There is no boot order: the printer waits until the greeting is provided. Disposing the greeter first stops it
providing (`L-Leave`), lets the printer unload while the greeting is still bound, and only then withdraws it
(`L-Unload`, behind its guard).

## From the paper to the code

| Paper | Here |
|---|---|
| Key `k` with value type `𝒱ₖ` | `Key[T]`, compared by identity |
| Effect function `𝔈Γ`, effect iterator `ℑΓ` (Defs. 8, 17) | an `Activation`: an inverse, an awaitable of one, or a sync or async iterator yielding them |
| Accumulator `φ`, `recover` (Defs. 2, 6) | `Accumulator`, applied last in first out |
| `effect` (Def. 12), Algorithm 1 | `ctx.effect(callback)`, returning an `Effect` disposed at most once |
| `set(k, v)` (Def. 20) | `ctx.provide(key, value)`: an effect whose inverse withdraws the binding |
| Provider function of metadata (Def. 26) | `ctx.provide_by(key, factory)` |
| `isolate`, `intercept` (Defs. 25, 27) | `ctx.isolate(key, realm)`, `ctx.intercept(key, metadata)`: derived contexts |
| Component `(d, p, e)` (Def. 48) | `Component(name, apply, requires, provides)` |
| Fiber `⟨d, p, e, π, σ, τ, θ⟩` (Def. 49) | `Fiber`: `component`, `parent`, `table`, `retired`, `state`, with `committed` as `ω` |
| `O-Insert` with instantiation (Def. 52) | `ctx.use(component)`, whose inverse is the child's `O-Retire` |
| `O-Retire`, `O-Remove` | `fiber.retire()`; removal is taken as soon as its premises hold |
| `L-Begin`, `L-Iter`, `L-Finish`, `L-Divert`, `L-Leave`, `L-Unload` | `Runtime.refresh` and `Runtime._drive` |
| Algorithm 6, proxy access | `ctx.get(key)`, checked against the declaring fiber's committed view |
| A commutative, witnessed coeffect (Def. 46) | `Entries`: each addition an entry of its own |

## Configuration, and running the example

The loader keeps a tree of entries and the fibers realizing it in step:

```python
from cordis_loader import Entry, Loader

loader = Loader(runtime.root)
await loader.load([
    Entry("events", "plugins.events:event_bus"),
    Entry("features", children=[Entry("greeter", "examples.bot.components:greeter", config={"greeting": "hey"})]),
    Entry("tenant", children=[...], isolate={STORE: True}),
])
print(loader.describe())          # how it fits together, building each component but running none
await loader.load(changed)        # reconciles by id: only what the change reaches reloads
await loader.reload(["examples.bot.components"], source_imports(["examples"]))  # hot module replacement
```

```sh
uv sync

uv run echo-server                    # an echo server; ECHO_SERVER=fastapi serves it with FastAPI instead
uv run curl-example http://127.0.0.1:8000/echo -d hello -v
uv run site-example --port 8080       # an items API; SITE_DATABASE=site.sqlite keeps items in SQLite
uv run site-example --routes          # the route table, without binding a port
uv run chat-server                    # an agent per websocket session; --tui adds a terminal client
uv run chat-server --describe         # how it fits together, application and a session, without starting it
uv run bot-example                    # a conversation, with the configuration changed twice while it runs
```

The chat server reads `ZAI_API_KEY` and `OPENAI_API_KEY` from the environment, and its tools look in
`WORKSPACE_ROOT`, or the current directory, never at what git ignores there.

## The microkernel's examples, in this framework's style

The four example programs of the `microkernel` project are rebuilt here, behaving the same and tested by the same
tests, ported. What changes is how they are put together, since this framework composes by effects and coeffects that
can be changed while the application runs, where the microkernel plans a fixed boot:

| microkernel | here |
|---|---|
| `kernel.register(...)`, `kernel.replace(TOKEN, plugin)` | a list of `Entry`s with stable ids; `patched(entries, Entry(id, ...))` swaps one, at runtime too through `Loader.load` |
| `kernel.describe()` | `describe(entries)`: the order a load from scratch activates them in, and why, without running anything |
| a capability introduced by one plugin | a key provided by one component |
| a slot, contributed before boot and collected | a key whose value is a table, each addition its own entry and its own effect (`RouteTable`, `Catalog`, `Arguments`, `Events`) |
| adapting a capability (`verbose` wrapping the HTTP requester) | observing it: `HTTP.observe` is a registration of its own, so it needs no place in any order |
| `server_cli` adapting the environment for the server | `server_cli` instantiating the server once the command line is parsed, with host and port bound into it |
| services, `END`, `STARTED`, `kernel.run()` | `run_application(entries)`: `serve(ctx, label, work)` for running work, `APPLICATION.end`, `when_started`; ending stops served work in dependency order, then unloads everything |
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

- Inverses run one at a time, newest first, as Theorem 16 has them; Cordis starts asynchronous disposers together.
- A second provider of a key in a realm is refused when it is inserted (O-Insert's premise), not when it provides.
- A failed fiber is not retried until it is revised (§4.4); `fiber.revise()` is that revision.
- Hot module replacement invalidates only the modules on a way from an entry to a change, rather than the entry's
  whole import tree: a key's identity is the object its module made, and Python cannot re-make it equal.

The Postgres tests need a server they may do anything to. They use the one `TEST_POSTGRES_DSN` names, or start a
throwaway `postgres:17-alpine` in Docker, and skip when there is neither.
