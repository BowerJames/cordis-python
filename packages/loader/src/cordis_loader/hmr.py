import ast
import importlib.util
import sys
from typing import Callable, Collection, Iterable

# The modules a module imports directly, among those that may be hot-replaced. Anything else is outside the graph, and
# is never invalidated: the standard library, installed packages, and whatever the composition marks external.
type Imports = Callable[[str], Collection[str]]

class RestartRequired(Exception):
    # A changed module that cannot be hot-replaced: only restarting the process picks it up.
    pass

def source_imports(roots: Collection[str]) -> Imports:
    # Reads a module's imports from its source, keeping those under one of roots (package names).
    def under_roots(name: str) -> bool:
        return any(name == root or name.startswith(f"{root}.") for root in roots)

    def is_module(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    def imports_of(name: str) -> Collection[str]:
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None or not spec.origin.endswith(".py"):
            return ()
        with open(spec.origin, encoding="utf-8") as file:
            tree = ast.parse(file.read(), spec.origin)
        package = name if spec.submodule_search_locations is not None else name.rpartition(".")[0]
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level \
                    else node.module or ""
                found.add(base)
                # from package import submodule names a module of its own.
                found.update(f"{base}.{alias.name}" for alias in node.names if is_module(f"{base}.{alias.name}"))
        return {module for module in found if under_roots(module) and module != name}

    return imports_of

def classify(
    stashed: Collection[str], externals: Collection[str], imports_of: Imports,
) -> tuple[set[str], set[str]]:
    # Algorithm 8: over the dependency subgraph of the changed modules, a module is accepted once one of its imports
    # is accepted, and declined once all of its imports are declined; one caught in an import cycle stays declined.
    accepted = set(stashed)
    declined = set(externals)
    pending: set[str] = set()
    for name in stashed:
        pending |= set(imports_of(name)) - accepted - declined
    progress = True
    while progress:
        progress = False
        for name in list(pending):
            imports = set(imports_of(name))
            if imports & accepted:
                accepted.add(name)
                pending.discard(name)
                progress = True
            elif imports <= declined:
                declined.add(name)
                pending.discard(name)
                progress = True
            else:
                pending |= imports - accepted - declined
    return accepted, declined | pending

def dependencies(root: str, declined: Collection[str], imports_of: Imports) -> set[str]:
    # Algorithm 9's traversal: every module root reaches, with the declined modules as a boundary.
    found: set[str] = set()

    def traverse(name: str) -> None:
        if name in found or name in declined:
            return
        found.add(name)
        for child in imports_of(name):
            traverse(child)

    traverse(root)
    return found

def stale(
    roots: Iterable[str], accepted: set[str], declined: Collection[str], imports_of: Imports,
) -> tuple[set[str], set[str]]:
    # Algorithm 9: the roots whose dependency tree reaches an accepted module, and the accepted set grown by the stale
    # modules of each such tree: those on a way from the root to a change. A module the change does not reach is left
    # alone, unlike a whole tree folded in: a key is the object a module makes, so executing an unchanged module again
    # would make a new key that nothing already running could resolve.
    grown = set(accepted)
    stale_roots: set[str] = set()
    for root in roots:
        tree = dependencies(root, declined, imports_of)
        if not tree & grown:
            continue
        stale_roots.add(root)
        progress = True
        while progress:
            progress = False
            for name in tree - grown:
                if set(imports_of(name)) & grown:
                    grown.add(name)
                    progress = True
    return stale_roots, grown

def invalidate(modules: Collection[str]) -> dict[str, object]:
    # Removes modules from the import cache, returning what was removed so it can be put back.
    importlib.invalidate_caches()
    return {name: sys.modules.pop(name) for name in modules if name in sys.modules}

def restore(modules: Collection[str], backup: dict[str, object]) -> None:
    for name in modules:
        sys.modules.pop(name, None)
    sys.modules.update(backup)  # type: ignore[arg-type]
