---
summary: Components declare the keys they require and provide, activate only once every required key is provided, follow their providers as they come and go, and tear down while what they depend on is still there.
---

# Wait, Don't Fail

## Context
Components depend on capabilities other components provide, and in a running system those providers appear, disappear and are replaced, in no order anyone arranged.

## Problem
A component that starts before what it needs exists crashes or runs half configured; one that keeps running after it is gone holds a reference to something dead; and one torn down because its provider left may need that provider to tear down.

## Forces
- **No boot order:** A running system has no startup to sequence, and a hand-kept order breaks with every change.
- **Identity:** A replacement provider may hold a value equal to the old one, and is still a different provider.
- **Teardown needs:** Releasing a resource often means handing it back to whatever provided it.
- **Liveness:** Whatever makes a provider wait for its consumers must never wait forever.

## Solution

Declare dependencies as keys (`requires`), and let a fiber activate only when each is provided by an active fiber (L-Begin; Theorem 70). When activation begins, the fiber commits a view: which fiber provided each key. It reads through that view for its whole episode, its teardown included, and reactivates exactly when the provider of some key changes identity: a fresh fiber is a new provider even when its value is equal.

Withdraw a capability in two moves. First the provider stops providing (L-Leave): its dependents' targets turn and they begin their own teardown while every binding it holds is still in place. Only when no installed fiber's committed view names it does it run its inverses (L-Unload, behind its guard). The guard cannot wait forever, since a fiber that has left can never be committed to again (Theorem 73).

An optional dependency is a nested fiber: `ctx.inject([key], apply)` adds behaviour while the key is provided and withdraws it when the key leaves. A dependency cycle is never satisfied, and `describe()` reports it from the declarations alone; split one member so what it provides is separate from what it requires.
