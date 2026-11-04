from typing import Any, Callable, Collection, Iterator, Sequence

from cordis import Component, Context, Fiber, FiberState, Inverse, Key, Metadata, Realm, RealmId
from cordis.describe import Declared, describe

from .entry import ComponentFactory, Entry, import_factory
from .hmr import Imports, RestartRequired, classify, invalidate, restore, stale

type Resolve = Callable[[str], ComponentFactory]

class _Node:
    # One entry and the fiber that realizes it. The entry is the identity that survives revision; each fiber is the
    # identity of one enablement of it.
    __slots__ = ("children", "ctx", "entry", "fiber", "intercepts")

    def __init__(self, entry: Entry) -> None:
        self.entry = entry
        self.fiber: Fiber | None = None
        # For a group, the context its children are instantiated through, while the group is active.
        self.ctx: Context | None = None
        self.children: dict[str, _Node] = {}
        # Held by reference in the entry's context, so an update to it is read at the next access, with no reload.
        self.intercepts: dict[Key[Any], Metadata] = dict(entry.intercept)

    @property
    def running(self) -> bool:
        return self.fiber is not None and not self.fiber.removed

def _effectful(ctx: Context) -> bool:
    return ctx.fiber.state in (FiberState.LOADING, FiberState.ACTIVE) and not ctx.fiber.removed

class Loader:
    # Keeps a tree of entries and the fibers realizing it in step (Section 5.2.1). The configuration is the authority:
    # the running system is changed only by giving the loader a new one, which it reconciles incrementally, and by
    # confluence (Theorem 80) the system then quiesces where loading the new configuration from scratch would have.
    def __init__(self, ctx: Context, resolve: Resolve = import_factory) -> None:
        self._root = _Node(Entry(id="", children=()))
        self._root.ctx = ctx
        self._resolve = resolve
        self._realms: dict[tuple[str, str, Key[Any]], Realm] = {}

    @property
    def entries(self) -> Sequence[Entry]:
        return [node.entry for node in self._root.children.values()]

    def fiber(self, *path: str) -> Fiber | None:
        # The fiber currently realizing the entry at path, a sequence of ids from the top of the tree, if it runs.
        node = self._root
        for id in path:
            if id not in node.children:
                return None
            node = node.children[id]
        return node.fiber if node.running else None

    def describe(self, entries: Sequence[Entry] | None = None) -> str:
        # How entries (the current ones unless given) fit together, without running anything: each entry's component
        # is built, which binds its configuration, but never applied, so this reads declarations alone.
        base = self._root.ctx
        assert base is not None
        nodes: list[Declared] = []

        def walk(entries: Sequence[Entry], parent: Declared | None, realms: dict[Key[Any], RealmId], off: bool) -> None:
            for entry in entries:
                here = dict(realms)
                for key, mode in entry.isolate.items():
                    here[key] = self._realm(entry, key, mode)
                disabled = off or entry.disabled
                if entry.children is not None:
                    group = Declared(entry.id, realms=here, parent=parent, inactive=disabled,
                                     state="disabled" if entry.disabled else f"group of {len(entry.children)}")
                    nodes.append(group)
                    walk(entry.children, group, here, disabled)
                    continue
                component = self._build(entry)
                keys = (*component.requires, *component.provides)
                nodes.append(Declared(
                    entry.id, tuple(component.requires), tuple(component.provides),
                    {key: here.get(key, base.realm(key)) for key in keys}, parent, inactive=disabled,
                    state="disabled" if entry.disabled else component.name,
                ))

        walk(self.entries if entries is None else entries, None, {}, False)
        return describe(nodes, tree="Entries")

    async def load(self, entries: Sequence[Entry]) -> None:
        # Reconciles the running system to entries. The instantiations and retirements are issued, and any fiber
        # being rebuilt has left, by the time this returns; await the runtime settling for the rest.
        Entry(id="", children=entries)  # checks the ids are distinct
        await self._reconcile(self._root, entries)

    async def reload(
        self, changed: Collection[str], imports_of: Imports, externals: Collection[str] = (),
    ) -> list[tuple[str, ...]]:
        # Hot module replacement (Section 5.2.2): replaces the entries whose modules reach a changed one, through
        # fiber operations alone, since a fiber already bounds every effect and coeffect of its component. It is a
        # transaction: every stale entry's module is imported anew before any fiber is touched, and if one fails to
        # import, the old modules are put back and nothing has changed. Returns the paths of the entries reloaded.
        accepted, declined = classify(changed, externals, imports_of)
        refused = sorted(name for name in changed if name in declined)
        if refused:
            raise RestartRequired(f"{', '.join(refused)} cannot be hot-replaced; restart to pick up the change")
        by_module: dict[str, list[tuple[tuple[str, ...], _Node, _Node]]] = {}
        for path, node, parent in self._walk(self._root, ()):
            if isinstance(node.entry.component, str):
                by_module.setdefault(node.entry.component.partition(":")[0], []).append((path, node, parent))
        stale_modules, accepted = stale(by_module, accepted, declined, imports_of)
        targets = [target for module in stale_modules for target in by_module[module]]
        backup = invalidate(accepted)
        try:
            for _, node, _ in targets:
                assert isinstance(node.entry.component, str)
                self._resolve(node.entry.component)
        except BaseException:
            restore(accepted, backup)
            raise
        for _, node, parent in targets:
            await self._disable(node)
            self._instantiate(node, parent)
        return sorted(path for path, _, _ in targets)

    def _walk(self, node: _Node, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], _Node, _Node]]:
        for id, child in node.children.items():
            yield (*path, id), child, node
            yield from self._walk(child, (*path, id))

    # --- reconciliation ------------------------------------------------------------------------------------------

    async def _reconcile(self, node: _Node, entries: Sequence[Entry]) -> None:
        # A keyed diff over child ids: what is gone is disabled, what is new is instantiated, what remains is updated.
        wanted = {entry.id for entry in entries}
        for id, child in list(node.children.items()):
            if id not in wanted:
                await self._disable(child)
        previous = node.children
        node.children = {}
        for entry in entries:
            existing = previous.get(entry.id)
            if existing is None:
                child = node.children[entry.id] = _Node(entry)
                self._instantiate(child, node)
            else:
                node.children[entry.id] = existing
                await self._update(existing, entry, node)

    async def _update(self, child: _Node, entry: Entry, parent: _Node) -> None:
        # Dispatches on which fields changed, taking the least disruptive operation each allows.
        old, child.entry = child.entry, entry
        if entry.disabled:
            await self._disable(child)
            return
        rebuild = (
            old.component != entry.component
            or old.config != entry.config
            or old.isolate != entry.isolate
            or old.is_group != entry.is_group
        )
        if rebuild:
            await self._disable(child)
        # Interception is consulted at read time (Section 5.2.1), so it is updated in place and nothing reloads.
        if child.intercepts != entry.intercept:
            child.intercepts.clear()
            child.intercepts.update(entry.intercept)
        if not child.running:
            self._instantiate(child, parent)
        elif entry.children is not None and child.ctx is not None:
            await self._reconcile(child, entry.children)

    async def _disable(self, node: _Node) -> None:
        if node.running and node.fiber is not None:
            await node.fiber.dispose()
        node.fiber = None
        node.ctx = None

    def _instantiate(self, node: _Node, parent: _Node) -> None:
        # Instantiates node's entry through its parent group's context. A group that is not running instantiates its
        # children itself when it next activates, from the entries it then holds.
        entry = node.entry
        if entry.disabled or parent.ctx is None or not _effectful(parent.ctx):
            return
        ctx = parent.ctx
        for key, mode in entry.isolate.items():
            ctx = ctx.isolate(key, self._realm(entry, key, mode))
        node.fiber = ctx.layered(node.intercepts).use(self._component(node))

    def _component(self, node: _Node) -> Component:
        if node.entry.children is not None:
            return Component(node.entry.id, apply=lambda ctx: self._mount(node, ctx))
        return self._build(node.entry)

    def _build(self, entry: Entry) -> Component:
        try:
            factory = self._resolve(entry.component) if isinstance(entry.component, str) else entry.component
            assert factory is not None
            return factory(**entry.config)
        except Exception as error:  # noqa: BLE001 - a bad entry fails on its own fiber, and its siblings still load
            failure = error

            def fail(ctx: Context) -> None:
                raise failure

            return Component(entry.id, apply=fail)

    def _mount(self, node: _Node, ctx: Context) -> Inverse:
        # A group's effect function: instantiate a child per entry it currently holds, through the group's context.
        node.ctx = ctx
        assert node.entry.children is not None
        previous = node.children
        node.children = {entry.id: previous.get(entry.id) or _Node(entry) for entry in node.entry.children}
        for entry in node.entry.children:
            child = node.children[entry.id]
            child.entry = entry
            child.intercepts.clear()
            child.intercepts.update(entry.intercept)
            self._instantiate(child, node)

        def unmount() -> None:
            # The group's accumulator retires the children it instantiated; this only forgets the context.
            node.ctx = None

        return unmount

    def _realm(self, entry: Entry, key: Key[Any], mode: bool | str) -> Realm:
        # A local realm is tagged by the entry's id, so it moves with the entry; a named one is shared by name.
        scope, name = ("local", entry.id) if mode is True else ("global", str(mode))
        return self._realms.setdefault((scope, name, key), Realm(name))
