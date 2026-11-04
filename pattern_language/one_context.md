---
summary: Everything a component does to anything shared goes through one context, as an operation on a key it declared, a key it provides, or a child it instantiates, so the runtime can revert every change and follow every dependency.
---

# One Context

## Context
Components are loaded, unloaded and reconfigured while the application runs, by people and by the application itself. Each changes state other components can see, and each depends on what others provide.

## Problem
A runtime can only revert the changes it can see, and only follow the dependencies it is told about, so a component that reaches around it (through a module-level variable, a closure over another component's context, a singleton) leaves changes behind when it unloads and breaks when what it reached for moves.

## Forces
- **Reach:** Anything importable is reachable, and a shortcut is always shorter than a declaration.
- **Recovery:** Unloading a component should leave the application as if the component had never run, and that is only decidable for what the runtime saw.
- **Ownership:** Shared state needs an owner whose lifetime bounds it; state owned by a module outlives every component that touched it.
- **Cost:** Routing every interaction through one mediator costs a lookup per access and a declaration per dependency.

## Solution

Give every component exactly one way to touch the world: its `Context`. What it reads it declares in `requires` and reads with `ctx.get`; what it shares it declares in `provides` and installs with `ctx.provide`; anything else it changes it changes inside `ctx.effect`, returning the undo; anything it loads it loads with `ctx.use`. This is the paper's context paradigm (§3.3): a component's behaviour is built from those stages alone (Definitions 30 and 56), and confinement follows from that form (Lemma 57).

When something shared has no key, give it one. A module-level cache becomes a component providing a cache key; a counter drawn from a global becomes a value bound at a key of its own. The test for the discipline is the paper's: unload the component and observe that nothing it did remains.

Some things never come inside. Writing bytes to a socket or a shared file is an emission that no inverse can take back (§6.1); acquire the channel as an effect, and treat what goes through it as irreversible.
