---
summary: Whoever provides a key makes its operations commute, by giving each registration an entry of its own and publishing nothing incidental, so any component can be unloaded at any time without disturbing the rest.
---

# Every Registration Gets Its Own Ticket

## Context
Many components register into one shared table: listeners on an event bus, routes on a server, tools in a registry. They load and unload independently, so one's registrations are removed while others', made before and after, remain.

## Problem
If two registrations of an equal value share an entry, or if the order of registrations is observable, removing one component's registrations disturbs another's, and unloading stops being local.

## Forces
- **Independence:** Theorem 43 lets inverses run in any order only if the effects commute.
- **Equality:** Two components may register the very same value.
- **Order:** Some tables are ordered by nature, such as a middleware chain, and order is what independent removal cannot survive.
- **Observability:** What an interface returns decides what can tell two states apart (Definition 31).

## Solution

Operations on different keys always commute (Theorem 45), so the obligation falls only on operations at one key, and only on its provider (Definition 46). Discharge it by representation: each addition draws an entry of its own, and its inverse removes that entry and no other, never "the entry equal to x". `Entries` is that table; the event bus, the timer, the route table and the catalogs keep their registrations in one.

The runtime cannot check the obligation (§5.1.1), and any component may keep a key, so state it once, as the contract's tests, and run them against every keeper. `cordis_plugins.testing.provided` loads one keeper on its own and hands back what it binds; each contract's tests are parametrised over a list of its keepers, so a new keeper adds itself to the list and is held to what the others are.

Publish only what callers need. An allocator whose handles no operation compares commutes; one returning comparable addresses, like POSIX `open` returning the lowest free descriptor, does not. Withholding an outcome callers do not need can move a key from one side of that line to the other. A contract is a record (Contracts Are Records), so what it publishes is exactly its fields: a route table counts the calls of each handler in progress and keeps the count to itself, offering components only what they call.

Keep ordered tables few, and give every position in one a reason: one component owning the whole sequence, whose accumulator orders it, or a declared dependency between components, which orders their activation.
