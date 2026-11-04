from dataclasses import dataclass
from typing import Callable, Generic, Mapping, TypeVar

# Interception metadata (Definition 26): merged per key by a monoid whose identity is the empty mapping.
type Metadata = Mapping[str, object]
type Merge = Callable[[Metadata, Metadata], Metadata]

T = TypeVar("T")

def merge_right(inner: Metadata, outer: Metadata) -> Metadata:
    # Right-biased: what the enclosing context says overrides what the component declared.
    return {**inner, **outer}

@dataclass(slots=True, frozen=True, eq=False)
class Key(Generic[T]):
    # A coeffect key k with its value type 𝒱ₖ. Its name is for people; its identity is the object, so two keys that
    # share a name never resolve to each other. The operations 𝒜ₖ are the methods of the value bound at the key, and
    # whoever provides the key owes the proof that they commute (Definition 46): give each registration its own entry.
    name: str
    merge: Merge = merge_right

    def __repr__(self) -> str:
        return f"Key({self.name!r})"

@dataclass(slots=True, frozen=True, eq=False)
class Realm:
    # An isolation realm (Definition 24). A key nobody isolated resolves in the realm that is the key itself.
    name: str

    def __repr__(self) -> str:
        return f"Realm({self.name!r})"

# What a key resolves to in one context: the key itself, or the Realm an enclosing context isolated it into. Compared
# by identity alone, as the paper's realm identifiers are.
type RealmId = object
