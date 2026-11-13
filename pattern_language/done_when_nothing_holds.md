---
summary: A component that is still doing something holds the runtime, as an effect of its own, and whoever runs a composition waits for the runtime to be done, which it is once it has settled and nothing holds it; ending early, failing and exiting with a status belong to the entrypoint, which says each in the open.
---

# Done When Nothing Holds

## Context
A composition is run by an entrypoint: it opens a runtime, loads the entries, and at some point has to return. Some compositions are for one piece of work (a request, a prompt), some serve until they are told to stop, and some are both: a terminal client beside a server.

## Problem
Node ends a process when no handle keeps it alive, so the TypeScript Cordis says nothing about when a program is over. asyncio ends a program when its main coroutine returns, whatever else is running, so here something has to say it. A runner that owns the answer (a start, a status, a list of services to stop, a failure policy) becomes a key every working component requires, keeps its state outside the runtime, and orders the ending a second time, beside the order unloading already follows.

## Forces
- **Liveness is not a task:** A timer yet to fire and a server on a thread of its own keep a program going as much as a task does.
- **Background work:** A watcher or an announcement beside a single request must not keep the program running.
- **One order:** Unloading already puts what relies on something before it (Theorem 70); a second order for the end of a run has to be kept in step with it.
- **Unloading is the common case:** Whatever makes an ending graceful has to hold for a reconfiguration too, or it holds only once.
- **The calculus:** No rule should read whether anything is still running, or a component could branch on it (Definition 55).
- **Closed worlds:** A few acts need everything else in place first, such as parsing a command line, and a running system has no "everything".

## Solution

Let a fiber hold the runtime while it is doing something: `ctx.hold(label)` is an effect whose inverse lets go, so unloading the fiber lets go too. Work is the usual case: `ctx.spawn(work, label)` runs `work(stopping)` as a task that holds while it runs, whose inverse tells it to stop, waits for it, and cancels it once its grace runs out, so an unloading never waits for ever. Work that runs beside what the composition is for takes `hold=False`. The runtime knows of holds and never of tasks.

The entrypoint waits for `runtime.done()`: settled, and nothing held. That is to work what quiescence is to transitions (Definition 53), an observation the orchestrator makes and no rule reads. Leaving the runtime's block then retires every fiber, and the guard orders the rest, so work that relies on a provider is stopped, still reading it, before the provider's is:

```python
failed = StopOnFailure()
async with open_runtime(on_error=failed) as runtime:
    with stop_on_signals(runtime):
        await Loader(runtime.root).load(entries)
        await runtime.done()
return failed.status()
```

Everything else about ending is the entrypoint's, and written there. A signal or a failure asks the runtime to stop, which only makes `done` return. A failure stays on its fiber (§4.4), and `ctx.contain(handler, fiber)` says who is told of it, so a session fails alone. The exit status is read off what failed; a component that decides it provides `EXIT_STATUS` for the entrypoint to read. A composition that runs for one of its parts beside a server waits for that part: `runtime.done(under=fiber)`.

What needs the rest in place is loaded after the rest has settled, by the entrypoint: the parser of a command line, a client that lists what a server offers. This is the one order a composition arranges, and a load from scratch of the same entries could differ from it, so it sits outside confluence (Theorem 80); it is kept to those entries and said in the open.

```python
await loader.load(without(entries, "args"))
await runtime.settled()
await loader.load(entries)
```

An order nothing declares is arranged the same way, as one more configuration to reconcile to: the chat server unloads its sessions before the rest, so each is heard to close by whatever listens.

Where one component's work calls another's code through a table, neither declares the other, and the guard cannot order them. The table's keeper does: withdrawing a route waits for the calls of its handler in progress, for no longer than the grace. That wait is outside what Theorem 73 proves, and the bound is what keeps it from deadlocking.
