---
summary: Every effect returns its own inverse at the moment it acts, the runtime accumulates them per fiber, and unloading applies them last in first out, so removing a component reverts everything it did without an uninstall path to write.
---

# Every Act Carries Its Undo

## Context
A component acquires resources and registers behaviour as it loads: connections, listeners, timers, bindings, children. It may be unloaded at any moment: by configuration, by a reload, because a dependency left, or because it failed half way through loading.

## Problem
When the code that releases a resource lives apart from the code that acquired it, cleanup goes missing, and a component cannot be removed without restarting what hosts it.

## Forces
- **Locality:** The code that knows what was acquired is the code that acquired it.
- **Order:** Releases depend on one another: the server stops taking requests before its pool is drained.
- **Interruption:** Loading may be abandoned between any two steps, and only the steps taken should be undone.
- **Trust:** The runtime cannot check that an inverse really reverts what its effect did.

## Solution

Make every effect a function that returns its inverse (Definition 8): `ctx.effect(open_watcher)`, where `open_watcher` starts a watcher and returns its `close`. The inverse is only held to reverting the effect at the state the effect produced, which is exactly what a closure over what was acquired does. The runtime composes each inverse onto the fiber's accumulator, and unloading applies the accumulator last in first out (Theorem 16), one inverse at a time, so each meets the state its own effect left.

Write a multi-step activation as a generator that yields each step's inverse as soon as the step is done. Between any two yields the runtime may stop, because the component's dependencies changed or it was retired, and recover exactly the steps taken (L-Divert). Where two steps must be released in a particular order that is not the reverse of acquisition, put both in one effect whose single inverse sequences them.

Every registry method returns the inverse of the registration it made. Because the runtime cannot verify an inverse, test it: dispose the fiber, and observe that what it registered is gone.
