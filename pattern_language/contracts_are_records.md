---
summary: The value bound at a key is a frozen record of operations, or a single function, declared beside the key in its contract module, so any component can keep the contract by binding operations of its own, and consumers see only what the contract offers.
---

# Contracts Are Records

## Context
A key is a contract between components that never meet: those providing it and those requiring it. Several components may keep one contract (SQL on SQLite or on Postgres, HTTP through httpx or requests, a stand-in under test), and a keeper is often a stateful object holding registrations.

## Problem
If the value at a key is a concrete class, every consumer is bound to that implementation: another keeper has to subclass it and inherit internals it does not want, and the whole of the class, including what only its keeper should call, is open to every consumer.

## Forces
- **Substitution:** A new keeper should need nothing from an existing one.
- **Width:** A keeper needs more than its consumers do: an application's runner starts it and stops what it serves, while components only hold it open, end it, report failures and serve work.
- **Expressiveness:** Some operations are generic in each call, such as publishing an event of a topic's type, or take defaults, and a plain `Callable` can type neither.
- **State:** What a keeper holds changes, so a field holding it would be out of date as soon as it was read.
- **Unverifiable promises:** The runtime cannot check that a keeper keeps the contract's promises, its commutativity among them (§5.1.1).
- **One convention:** Two ways of writing a contract are two things to learn, and a choice to make each time.

## Solution

A contract module holds the key, the type of the value bound at it, the data both sides exchange (`Request`, `Route`, `Topic`), and the helpers that use the contract (`serve`), and says in a comment the promises every keeper keeps. The value is a single function (`GET_ENV`, `STDOUT`, `ARGS`) or a `@dataclass(slots=True, frozen=True)` record of operations (`SQL`, `ROUTES`, `APPLICATION`). Each field is a `Callable`, or, where the operation is generic or has defaults, a `Protocol` defining nothing but `__call__` (`Subscribe`, `Serve`, `Execute`). What changes is read through a call, never kept in a field: `routes()`, `names()`, `pending()`.

A keeper implements the contract however it likes, usually as a class private to its component module, and binds a record built from that object's bound methods:

```python
timers = _LoopTimer()
ctx.provide(TIMER, Timer(after=timers.after, every=timers.every, pending=timers.pending))
```

The record carries what consumers need and nothing more; what only the keeper uses stays on its own object, as `ApplicationRun` keeps starting the run, stopping what is served and the status it ended with. Being frozen, the record cannot be changed by those holding it (One Context).

State the promises a second time, as tests. Each contract's tests are parametrised over a list of the components keeping it and reach the value through `plugins.testing.provided`, never through a keeper's class, so a new keeper adds itself to the list and is held to what the others are (`test_sql.py`, `test_events.py`).
