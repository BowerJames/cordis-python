---
summary: Components import contracts and never other components, a contract's helpers live with the contract, and only compositions name the components keeping each contract, passing a choice in as configuration where one component instantiates another.
---

# Only Compositions Name Components

## Context
An application is a composition: a list of entries, built by a function its entrypoint calls and loaded by the loader. Each entry names a component, which keeps some contracts and requires others.

## Problem
A component module that imports another component, even only as a default, chooses an implementation where no composition can see it, and swapping that implementation becomes an edit to the component rather than a revision of the composition.

## Forces
- **Substitution:** Whatever keeps the same contract should be able to stand in, in production, under test and on a developer's machine.
- **Visibility:** What runs should be readable from the composition alone, and changeable by revising it.
- **Convenience:** A default saves every composition a line.
- **Helpers:** Some functions exist only to use a contract well, such as making the arguments a command line is given, and belong to no keeper.
- **Identity:** Keys are compared by identity, so every composition, a test's and a description's included, must meet on the same objects.

## Solution

Let imports run one way. A contract module imports the kernel and other contracts. A component module imports contracts, together with the helpers that use them, which live in the contract module beside the key they serve: `option` and `switch` make the arguments `CLI` takes, so they belong to that contract, and a component with a flag of its own imports them from there rather than from the parser. Only compositions import components.

Lay a project out along the same line, so the direction can be read from the folders: `contracts/` holds the keys its components meet on, with the records bound at them; `components/` holds the components, each module importing from `contracts/`, from the libraries' contracts, and from plain helpers beside it; and `entrypoints/` holds a module per deliverable, building its composition and running it until it is done (Done When Nothing Holds), the only place components are named. A project whose components keep only contracts it gets from a library has no `contracts/` of its own. Each project under `examples/` is laid out this way.

A component that instantiates another takes it as configuration rather than importing it. `server_cli(server)` instantiates whatever server it is given, once the command line has been parsed, and the items project's composition gives it one:

```python
Entry("server", server_cli, config={"server": asyncio_http_server})
```

Swapping the server is then a revision of that entry, which the loader reconciles and `describe` shows.

Compositions choose components but never make keys: keys come from the contract modules (Configuration is the Truth). A plain helper that is no component and keeps no contract, such as reading what a repository's ignore files hide, may be imported by whichever components use it.
