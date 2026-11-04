from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .key import Key, Realm, RealmId

# What describe reads is declarations alone, never what an effect did, so describing runs nothing and gives the same
# text for the same declarations. The order is the canonical form of Theorem 80: one episode of each fiber, providers
# before what requires them, which is the order a load from scratch activates them in.

@dataclass(slots=True, frozen=True, eq=False)
class Declared:
    # One fiber, or one that would be: its component's declarations, the realm each declared key resolves in, where
    # it stands in the tree, and, for a fiber that exists, how it is doing.
    name: str
    requires: Sequence[Key[Any]] = ()
    provides: Sequence[Key[Any]] = ()
    realms: Mapping[Key[Any], RealmId] = field(default_factory=dict)
    parent: "Declared | None" = None
    state: str | None = None
    # Left out of the order: a retired fiber, a disabled entry.
    inactive: bool = False

    def realm(self, key: Key[Any]) -> RealmId:
        return self.realms.get(key, key)

def _owners(nodes: Sequence[Declared]) -> dict[RealmId, Declared]:
    return {node.realm(key): node for node in nodes if not node.inactive for key in node.provides}

def dependencies(nodes: Sequence[Declared], node: Declared) -> list[tuple[Key[Any], Declared | None]]:
    # Each key node requires, with the one node that may provide it in node's realm, if there is one.
    owners = _owners(nodes)
    return [(key, owners.get(node.realm(key))) for key in node.requires]

def cycles(nodes: Sequence[Declared]) -> list[list[tuple[Declared, Key[Any]]]]:
    # Every dependency cycle, each as the path around it, found from the declarations alone (Section 6.5): a cycle is
    # never satisfied, whatever the schedule.
    owners = _owners(nodes)
    found: list[list[tuple[Declared, Key[Any]]]] = []
    done: set[Declared] = set()

    def visit(node: Declared, path: list[tuple[Declared, Key[Any]]], on_path: dict[Declared, int]) -> None:
        on_path[node] = len(path)
        for key in node.requires:
            provider = owners.get(node.realm(key))
            if provider is None or provider in done:
                continue
            step = [*path, (node, key)]
            if provider in on_path:
                cycle = step[on_path[provider]:]
                if not any({member for member, _ in cycle} == {member for member, _ in other} for other in found):
                    found.append(cycle)
            else:
                visit(provider, step, on_path)
        del on_path[node]
        done.add(node)

    for node in nodes:
        if node not in done and not node.inactive:
            visit(node, [], {})
    return found

def activation_order(nodes: Sequence[Declared]) -> list[Declared]:
    # Providers before what requires them, parents before their children, otherwise in the order given; what can
    # never activate (waiting on a key nobody provides, in a cycle, or beneath something that cannot) is left out.
    blocked = {node for cycle in cycles(nodes) for node, _ in cycle}
    order: list[Declared] = []
    placed: set[Declared] = set()
    remaining = [node for node in nodes if not node.inactive and node not in blocked]
    progress = True
    while progress:
        progress = False
        for node in list(remaining):
            ready = all(provider in placed for _, provider in dependencies(nodes, node)) and (
                node.parent is None or node.parent in placed
            )
            if ready:
                order.append(node)
                placed.add(node)
                remaining.remove(node)
                progress = True
    return order

def _labels(nodes: Sequence[Declared]) -> tuple[Callable[[Key[Any], RealmId], str], dict[Declared, str]]:
    # Keys and fibers sharing a name are told apart by a number, in the order they are first met; a key isolated into
    # a realm says which.
    named: dict[str, list[Key[Any]]] = {}
    for node in nodes:
        for key in (*node.requires, *node.provides):
            known = named.setdefault(key.name, [])
            if all(other is not key for other in known):
                known.append(key)

    def key_label(key: Key[Any], realm: RealmId) -> str:
        known = named.get(key.name, [key])
        shown = key.name if len(known) < 2 else f"{key.name}#{next(i for i, k in enumerate(known, 1) if k is key)}"
        return f"{shown} in {realm.name}" if isinstance(realm, Realm) else shown

    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.name] = counts.get(node.name, 0) + 1
    seen: dict[str, int] = {}
    labels: dict[Declared, str] = {}
    for node in nodes:
        if counts[node.name] > 1:
            seen[node.name] = seen.get(node.name, 0) + 1
            labels[node] = f"{node.name}#{seen[node.name]}"
        else:
            labels[node] = node.name
    return key_label, labels

def describe(nodes: Sequence[Declared], tree: str = "Fibers") -> str:
    key_label, label = _labels(nodes)
    sections: list[str] = []

    problems: list[str] = []
    for node in nodes:
        if node.inactive:
            continue
        for key, provider in dependencies(nodes, node):
            if provider is None:
                problems.append(f"  - {label[node]} requires {key_label(key, node.realm(key))}, which nothing provides")
    for cycle in cycles(nodes):
        path = " ".join(f"{label[node]} -[{key_label(key, node.realm(key))}]->" for node, key in cycle)
        problems.append(
            f"  - {path} {label[cycle[0][0]]} form a cycle, so none of them can activate; split one so what it "
            "provides is separate from what it requires"
        )
    if problems:
        sections.append("\n".join(["Problems:", *problems]))

    order = activation_order(nodes)
    if order:
        lines = ["Activation order (unloaded in reverse):"]
        width = max(len(label[node]) for node in order)
        digits = len(str(len(order)))
        for at, node in enumerate(order, start=1):
            reasons = ", ".join(
                f"{label[provider]} ({key_label(key, node.realm(key))})"
                for key, provider in dependencies(nodes, node) if provider is not None
            )
            line = f"  {at:>{digits}}. {label[node]}"
            lines.append(f"{line:<{width + digits + 5}}after {reasons}" if reasons else line)
        sections.append("\n".join(lines))

    if any(node.state is not None for node in nodes):
        rows: list[tuple[str, str]] = []

        def walk(parent: Declared | None, depth: int) -> None:
            for node in nodes:
                if node.parent is parent:
                    rows.append(("  " * depth + label[node], node.state or ""))
                    walk(node, depth + 1)

        walk(None, 1)
        width = max(len(name) for name, _ in rows)
        sections.append("\n".join([f"{tree}:", *(f"{name:<{width}}  {state}".rstrip() for name, state in rows)]))

    keys: dict[tuple[int, int], tuple[str, list[str], list[str]]] = {}
    for node in nodes:
        if node.inactive:
            continue
        for provided, declared in ((True, node.provides), (False, node.requires)):
            for key in declared:
                realm = node.realm(key)
                _, providers, requirers = keys.setdefault((id(key), id(realm)), (key_label(key, realm), [], []))
                (providers if provided else requirers).append(label[node])
    if keys:
        width = max(len(name) for name, _, _ in keys.values())
        lines = ["Keys:"]
        for name, providers, requirers in keys.values():
            parts = [f"provided by {', '.join(providers)}" if providers else "not provided"]
            if requirers:
                parts.append(f"required by {', '.join(requirers)}")
            lines.append(f"  {name:<{width}}  {'; '.join(parts)}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections) if sections else "Nothing is registered."
