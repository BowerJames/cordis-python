import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from cordis import Component, Key, Metadata

# Builds the component an entry declares from the entry's config, given as keyword arguments. Configuration is bound
# into the component's effect function here, so one factory given two configs makes two components (Section 4.4).
type ComponentFactory = Callable[..., Component]
# A factory, or where to import one from: "package.module:attribute".
type ComponentRef = str | ComponentFactory

@dataclass(slots=True, frozen=True)
class Entry:
    # One fiber, declared (Definition 81). An entry names either a component or children, which it loads as a group.
    id: str
    component: ComponentRef | None = None
    config: Mapping[str, Any] = field(default_factory=dict)
    children: Sequence["Entry"] | None = None
    disabled: bool = False
    # Per key: True for a realm private to this entry, which moves with it; a name for a realm shared by every entry
    # naming it.
    isolate: Mapping[Key[Any], bool | str] = field(default_factory=dict)
    intercept: Mapping[Key[Any], Metadata] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.component is None) == (self.children is None):
            raise ValueError(f"entry {self.id!r} must name a component or children, and not both")
        if self.children is not None and self.config:
            raise ValueError(f"entry {self.id!r} is a group, whose config is its children")
        ids = [child.id for child in self.children or ()]
        duplicates = sorted({id for id in ids if ids.count(id) > 1})
        if duplicates:
            raise ValueError(f"entry {self.id!r} has more than one child called {', '.join(duplicates)}")

    @property
    def is_group(self) -> bool:
        return self.children is not None

def import_factory(ref: str) -> ComponentFactory:
    module_name, _, attribute = ref.partition(":")
    if not attribute:
        raise ValueError(f"component reference {ref!r} names no attribute; write it as module:attribute")
    factory: ComponentFactory = getattr(importlib.import_module(module_name), attribute)
    return factory

def entries_from(data: Sequence[Mapping[str, Any]], keys: Mapping[str, Key[Any]]) -> list[Entry]:
    # Entries from plain data, as JSON or YAML would give them. Keys are named, and each name must be one keys knows,
    # since a key's identity is the object and not its name.
    def key(name: str, entry_id: str) -> Key[Any]:
        if name not in keys:
            raise ValueError(f"entry {entry_id!r} names key {name!r}, which is not one of {', '.join(sorted(keys))}")
        return keys[name]

    def one(item: Mapping[str, Any]) -> Entry:
        unknown = set(item) - {"id", "component", "config", "children", "disabled", "isolate", "intercept"}
        if unknown:
            raise ValueError(f"entry {item.get('id')!r} has unknown fields: {', '.join(sorted(unknown))}")
        entry_id = str(item["id"])
        children = item.get("children")
        return Entry(
            id=entry_id,
            component=item.get("component"),
            config=dict(item.get("config", {})),
            children=[one(child) for child in children] if children is not None else None,
            disabled=bool(item.get("disabled", False)),
            isolate={key(name, entry_id): mode for name, mode in item.get("isolate", {}).items()},
            intercept={key(name, entry_id): dict(metadata) for name, metadata in item.get("intercept", {}).items()},
        )

    return [one(item) for item in data]
