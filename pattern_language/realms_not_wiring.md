---
summary: Two of something, such as a second server or a session per connection, are the same components loaded in a group whose own keys are isolated into realms of their own, so nothing is handed a key, the components do not change, and the group's fiber bounds its lifetime and, contained, its failures.
---

# Realms, Not Wiring

## Context
A key has one provider in a realm (O-Insert), and components find what they need by key rather than being handed it. Yet an application often runs more than one of something: an admin server beside the public one, a session for each connection, each with a connection, model and tools of its own.

## Problem
Handing each instance the keys it should use brings back wiring: every factory grows a parameter for each key it touches, and the composition has to thread the same objects through all of them. Giving each repeated part a mechanism of its own puts in the kernel what composition can already say.

## Forces
- **One provider per realm:** A second provider of a key in the same realm is refused when it is inserted.
- **Sharing:** A session shares most of what the application has (the workspace, the providers on offer, the event bus) and binds only a few keys for itself.
- **Unchanged components:** The same server or tool should run alone or as one of many, without knowing which.
- **Lifetime:** A session lasts as long as its connection, and everything it loaded must leave with it.
- **Failure:** A session that fails must fail alone.
- **Checking:** What can never be resolved should be reported, not waited on forever.

## Solution

Put what repeats in a group, and isolate the keys the group binds for itself (Definitions 24 and 25; §4.4, Isolation). In a configuration the group is an entry:

```python
Entry("admin", children=[Entry("routes", route_table), Entry("server", asyncio_http_server), Entry("health", health)],
      isolate={ROUTES: True, SERVER_ADDRESS: True})
```

runs a second route table and server, unchanged, whose routes reach that table alone. While the application runs, the group is a fiber: chat isolates each key in `SESSION_KEYS` on its context, uses a session component providing what the session is opened with (`CONNECTION`, `TOOLS`), and loads the session's entries beneath it with a loader of its own. A key that is not isolated resolves as before, so every session uses the application's workspace and event bus.

The fiber bounds the session's lifetime: disposing it reverts everything the session loaded. `ctx.contain` bounds its failures: a failure anywhere beneath the fiber goes to the handler its opener gave, and the application runs on. A key that should have been isolated and was not is found at once, since a second session's provider of it is refused when inserted.

What cannot be checked before the group exists is checked as it settles. `describe` reports a key nobody provides from the declarations alone, and chat, once a session has settled, reports each member still waiting on a key as that session's failure (`_unsatisfied`). Keep the isolated keys listed beside the contracts they name, as `SESSION_KEYS` is in chat's contracts, so that adding a key a session binds and isolating it are one change.
