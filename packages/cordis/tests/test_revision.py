from cordis import Component, Context, FiberState, Inverse, Key, Runtime

from .helpers import state

GREETING: Key[str] = Key("greeting")

def greeter(text: str) -> Component:
    # Configuration is bound into apply, so the same component given new text is a new component.
    def apply(ctx: Context) -> None:
        ctx.provide(GREETING, text)
    return Component("greeter", apply=apply, provides=(GREETING,))

def listener(log: list[str]) -> Component:
    def apply(ctx: Context) -> Inverse:
        log.append(f"heard {ctx.get(GREETING)}")
        return lambda: log.append(f"stopped hearing {ctx.get(GREETING)}")
    return Component("listener", apply=apply, requires=(GREETING,))


async def test_revising_a_provider_reactivates_its_dependents_against_the_new_one(runtime: Runtime) -> None:
    log: list[str] = []
    runtime.root.use(listener(log))
    fiber = runtime.root.use(greeter("hello"))
    await runtime.settled()
    revised = await fiber.revise(greeter("hi"))
    await runtime.settled()
    assert log == ["heard hello", "stopped hearing hello", "heard hi"]
    assert fiber.removed
    assert state(revised) is FiberState.ACTIVE
    assert revised.uid != fiber.uid

async def test_a_failed_fiber_is_retried_by_revising_it(runtime: Runtime) -> None:
    attempts: list[str] = []

    def flaky(ctx: Context) -> None:
        attempts.append("attempt")
        if len(attempts) == 1:
            raise RuntimeError("first attempt fails")

    fiber = runtime.root.use(Component("flaky", apply=flaky))
    await runtime.settled()
    assert state(fiber) is FiberState.FAILED
    retried = await fiber.revise()
    await runtime.settled()
    assert attempts == ["attempt", "attempt"]
    assert state(retried) is FiberState.ACTIVE
    assert retried.error is None

async def test_a_revision_keeps_the_place_the_fiber_stood_in(runtime: Runtime) -> None:
    def group(ctx: Context) -> None:
        ctx.isolate(GREETING).use(greeter("inside"))

    parent = runtime.root.use(Component("group", apply=group))
    await runtime.settled()
    (child,) = runtime.children(parent)
    revised = await child.revise(greeter("revised"))
    await runtime.settled()
    assert revised.parent is parent
    assert revised.realms[GREETING] is child.realms[GREETING]
    assert runtime.root.find(GREETING) is None
    await parent.dispose()
    assert runtime.fibers() == ()
