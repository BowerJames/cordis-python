from cordis import Component, Context, FiberState, Inverse, Key, Runtime

from .helpers import state

JOBS: Key[list[str]] = Key("jobs")

def nest(log: list[str], name: str, *children: Component) -> Component:
    def apply(ctx: Context) -> Inverse:
        log.append(f"{name} up")
        for child in children:
            ctx.use(child)
        return lambda: log.append(f"{name} down")
    return Component(name, apply=apply)


async def test_unloading_a_parent_retires_everything_it_instantiated(runtime: Runtime) -> None:
    # Definition 52: an instantiation is one of the parent's effects, its inverse the child's O-Retire.
    log: list[str] = []
    root = runtime.root.use(nest(log, "a", nest(log, "b", nest(log, "c")), nest(log, "d")))
    await runtime.settled()
    assert sorted(log) == ["a up", "b up", "c up", "d up"]
    assert len(runtime.fibers()) == 4
    await root.dispose()
    assert sorted(entry for entry in log if entry.endswith("down")) == ["a down", "b down", "c down", "d down"]
    assert runtime.fibers() == ()

async def test_a_parent_leaves_the_registry_only_after_its_children(runtime: Runtime) -> None:
    # O-Remove removes children before their parent, so the fiber tree stays well formed throughout.
    log: list[str] = []
    parent = runtime.root.use(nest(log, "parent", nest(log, "child")))
    await runtime.settled()
    (child,) = runtime.children(parent)
    removed: list[str] = []
    parent.retire()
    while not parent.removed:
        await runtime.wait_for(lambda: child.removed or parent.removed)
        if child.removed and "child" not in removed:
            removed.append("child")
    removed.append("parent")
    assert removed == ["child", "parent"]

async def test_a_child_disposed_on_its_own_is_not_retired_again(runtime: Runtime) -> None:
    log: list[str] = []
    parent = runtime.root.use(nest(log, "parent", nest(log, "child")))
    await runtime.settled()
    (child,) = runtime.children(parent)
    await child.dispose()
    assert state(parent) is FiberState.ACTIVE
    assert len(parent.accumulator) == 1  # only parent's own inverse is left
    await parent.dispose()
    assert log.count("child down") == 1

async def test_inject_adds_behaviour_while_an_optional_key_is_provided(runtime: Runtime) -> None:
    # The optional-dependency pattern: a sub-fiber that requires the key, under a parent that does not.
    log: list[str] = []

    def tool(ctx: Context) -> None:
        log.append("foreground tool")

        def upgraded(inner: Context) -> Inverse:
            log.append(f"tool with {len(inner.get(JOBS))} jobs")
            return lambda: log.append("tool without jobs")

        ctx.inject([JOBS], upgraded)

    def jobs(ctx: Context) -> None:
        ctx.provide(JOBS, ["job"])

    tool_fiber = runtime.root.use(Component("tool", apply=tool))
    await runtime.settled()
    registry = runtime.root.use(Component("jobs", apply=jobs, provides=(JOBS,)))
    await runtime.settled()
    await registry.dispose()
    assert log == ["foreground tool", "tool with 1 jobs", "tool without jobs"]
    assert state(tool_fiber) is FiberState.ACTIVE

async def test_a_child_reads_what_its_parent_committed(runtime: Runtime) -> None:
    seen: list[list[str]] = []

    def jobs(ctx: Context) -> None:
        ctx.provide(JOBS, ["job"])

    def parent(ctx: Context) -> None:
        ctx.use(Component("child", apply=lambda inner: seen.append(inner.get(JOBS))))

    runtime.root.use(Component("jobs", apply=jobs, provides=(JOBS,)))
    runtime.root.use(Component("parent", apply=parent, requires=(JOBS,)))
    await runtime.settled()
    assert seen == [["job"]]
