from typing import TYPE_CHECKING, Any, Mapping

from .key import Key, Metadata, RealmId

if TYPE_CHECKING:
    from .context import Context
    from .runtime import Fiber, Runtime

# What an orchestrator reaches beside the Fiber handles that use gives back: the loader of Section 5.2, which builds
# fibers from a configuration, or a test inspecting what it ran. A component never needs any of it. What it touches it
# touches through its context, which offers only the operations the calculus models (Section 3.3).

def runtime_of(ctx: "Context") -> "Runtime":
    return ctx._runtime

def fiber_of(ctx: "Context") -> "Fiber":
    # The fiber owning ctx's effects.
    return ctx._fiber

def realm_of(ctx: "Context", key: Key[Any]) -> RealmId:
    # The realm key resolves in beneath ctx: ρ(k) of Definition 25.
    return ctx._realm(key)

def layered(ctx: "Context", layer: Mapping[Key[Any], Metadata]) -> "Context":
    # Intercepts every key layer names at once, holding the layer by reference: whoever owns it may change it, and
    # every access beneath the derived context reads it as it then stands. It is how the loader updates interception
    # without reloading anything (Section 5.2.1); a component intercepts with Context.intercept.
    return ctx._layered(layer)
