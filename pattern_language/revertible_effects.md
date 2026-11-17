---
summary: Every effect returns its own inverse at the moment it acts, the component yields each one from its effect function, the runtime accumulates them per fiber, and unloading applies them last in first out, so removing a component reverts everything it did without an uninstall path to write, and the effect function reads as the list of what will be undone.
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
- **Awareness:** An inverse taken out of sight, by a helper that tracks it on the component's behalf, is one the component's author never sees, and cannot order or reason about.
- **Forgetting:** An inverse handed back is one a caller can drop.

## Solution

Make every effect return its inverse (Definition 8), and make the component yield it. An effect function is a generator that yields each step's inverse as soon as the step is done: a watcher it starts, then `yield watcher.close`; a key it provides, `yield ctx.provide(KEY, value)`; a child, `yield ctx.use(child).retire`; a route, `yield ctx.get(ROUTES).add(route)`. The inverse is only held to reverting the effect at the state the effect produced, which is exactly what a closure over what was acquired does. The runtime composes each yielded inverse onto the fiber's accumulator, and unloading applies the accumulator last in first out (Theorem 16), one inverse at a time, so each meets the state its own effect left. Nothing is tracked behind the component's back: reading its effect function is reading its teardown, backwards.

Every yield is a seam. Between any two the runtime may stop, because the component's dependencies changed or it was retired, and recover exactly the steps taken (L-Divert). That only holds if, at every seam, the accumulator holds the inverse of everything done so far, so an inverse is yielded at once, never kept for later. Where two steps must be released in an order that is not the reverse of acquisition, yield one inverse that sequences them; where a helper performs several effects, it returns one inverse for them all (`compose`).

Whatever performs an effect on a component's behalf returns an `Undo`, an inverse that knows whether anything holds it. Who owes it is decided by when it is made, not by what made it: while the runtime drives a step of a fiber's effect function, every `Undo` made is owed by that fiber, so a registry reached through a key needs no hook into the runtime, and `provide` and `use` outside any step are owed by their context's fiber. The runtime checks at every seam that everything the fiber owes is held, and fails an activation that left one behind (`UnyieldedInverse`), reverting it all the same; a task the step started does not carry the step on, so what it makes later is owed by nobody until adopted; an `Undo` dropped without ever being held warns (`DroppedInverse`), as a coroutine never awaited does. Where there is nothing to yield to, because the fiber is already active (a session opened per connection) or the caller orchestrates (the loader), `ctx.adopt` holds the inverse at once. Applying an `Undo` reverts its one effect early and takes it off the accumulator, so it is never applied twice.

Every registry method returns the `Undo` of the registration it made. Because the runtime cannot verify an inverse, test it: dispose the fiber, and observe that what it registered is gone.
