---
summary: The running system is described by a tree of entries and changed only by reconciling it to a new one, which ends where loading the new tree from scratch would; module changes are applied the same way, through the fibers they reach.
---

# Configuration is the Truth

## Context
An application is assembled from components chosen, configured and arranged by its operators, and both the arrangement and the components' code change while it runs.

## Problem
A system edited in place drifts from any description of it, until nobody can say what a restart would produce, and restarting to pick up a change throws away everything the change did not touch.

## Forces
- **Authority:** There must be one record of what is meant to run.
- **Disruption:** A change should reload what it reaches and nothing else.
- **Equivalence:** A reconfigured system and a freshly loaded one should be the same system.
- **Identity:** In Python a key is the object its module made, so executing an unchanged module again makes keys nothing running can resolve.
- **Equality:** Reconciling compares an entry's fields with those of the entry it replaces, and a key compares by identity, so a key made while composing is a new key at every composition.

## Solution

Describe the application as `Entry`s with stable ids, and change it only through `Loader.load`, which reconciles by id: an entry that is gone is unloaded, a new one instantiated, and a changed one given the least disruptive operation its changed fields allow (a rebuild for its component or isolation; for its configuration, the new payload handed to the fiber; an in-place update for interception, read at the next access). By confluence (Theorem 80) the system quiesces where loading the final configuration from scratch would, so every shorter route is answerable to that endpoint. `Loader.describe` says how a configuration fits together, building each configuration but running nothing.

Keep configuration out of the component. A component is a value, and its effect function is applied to the configuration its fiber holds (`apply(ctx, config)`, Algorithm 4), built and checked from the entry's `config` by the component's schema as the entry loads. The component then stays the same object across every configuration, so a change of configuration is a change of configuration and not of component, and the component can be asked what it makes of it (§5.2.1). By default nothing: the fiber is revised, retired and reinserted, and its dependents reactivate against the new fiber. A component that can do better yields `ctx.accept(handler)`, and the handler takes a new configuration in place when the fiber then runs as one activated with it would, which is the endpoint a revision would reach. The fiber keeps what it took, so a later reactivation applies it. What it cannot take in place, such as a change of what it provides, it declines. Configuration that changes what a component declares (its `requires` or `provides`) is no configuration at all but a different component, built by a function and named by the entry.

Apply code changes the same way (`Loader.reload`, §5.2.2): classify the changed modules, find the entries whose modules reach a change, import their modules anew before touching any fiber, and only then replace those fibers, rolling every module back if any import fails. Invalidate only the modules on a way from an entry to a change, and keep keys in modules of their own that rarely change, so that every key keeps its identity across a reload.

Those modules are the contracts (Contracts Are Records), and a key is made there and nowhere else. One made while composing would be new each time the composition is built: every entry configured with it would compare changed and be rebuilt on every reconciliation, a component built from one composition could never meet one built from another, and a configuration read from data, which names its keys (`entries_from`), would have nothing to name. What a composition chooses is which components keep the contracts, passed in as configuration (Only Compositions Name Components).
