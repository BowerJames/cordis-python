from cordis import Component, Context, FiberState, Key, Metadata, Realm, Runtime

from .helpers import state

STORAGE: Key[str] = Key("storage")
FILES: Key[str] = Key("files")

def storage(path: str) -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(STORAGE, path)
    return Component(f"storage {path}", apply=apply, provides=(STORAGE,))

def bot(name: str, seen: list[str]) -> Component:
    return Component(name, apply=lambda ctx: seen.append(f"{name} uses {ctx.get(STORAGE)}"), requires=(STORAGE,))


async def test_an_isolated_realm_binds_a_key_independently(runtime: Runtime) -> None:
    # Coeffect isolation (Definition 24): the same key resolves to different bindings in different subtrees, and
    # O-Insert's disjointness is read within a realm, so both providers are admitted.
    seen: list[str] = []
    tenant = runtime.root.isolate(STORAGE)
    runtime.root.use(storage("shared.db"))
    tenant.use(storage("tenant.db"))
    runtime.root.use(bot("public", seen))
    tenant.use(bot("private", seen))
    await runtime.settled()
    assert sorted(seen) == ["private uses tenant.db", "public uses shared.db"]

async def test_a_change_in_one_realm_reaches_only_the_dependents_in_it(runtime: Runtime) -> None:
    seen: list[str] = []
    tenant = runtime.root.isolate(STORAGE)
    shared = runtime.root.use(storage("shared.db"))
    tenant.use(storage("tenant.db"))
    public = runtime.root.use(bot("public", seen))
    private = tenant.use(bot("private", seen))
    await runtime.settled()
    await shared.dispose()
    assert state(public) is FiberState.INACTIVE
    assert state(private) is FiberState.ACTIVE

async def test_contexts_naming_one_realm_share_its_binding(runtime: Runtime) -> None:
    seen: list[str] = []
    realm = Realm("team")
    runtime.root.isolate(STORAGE, realm).use(storage("team.db"))
    runtime.root.isolate(STORAGE, realm).use(bot("member", seen))
    await runtime.settled()
    assert seen == ["member uses team.db"]

async def test_interception_merges_declared_metadata_under_the_contexts(runtime: Runtime) -> None:
    # Definition 27: the provider is applied to d(k) ⊕ ι(k), right-biased, so an enclosing context can narrow how a
    # component uses a coeffect without changing either party.
    served: list[Metadata] = []

    def files(ctx: Context) -> None:
        def serve(metadata: Metadata) -> str:
            served.append(metadata)
            return "read-only" if metadata.get("readonly") else "read-write"
        ctx.provide_by(FILES, serve)

    modes: list[str] = []
    runtime.root.use(Component("files", apply=files, provides=(FILES,)))
    reader = Component(
        "reader",
        apply=lambda ctx: modes.append(ctx.get(FILES)),
        requires=(FILES,),
        metadata={FILES: {"readonly": False, "root": "/srv"}},
    )
    runtime.root.use(reader)
    runtime.root.intercept(FILES, {"readonly": True}).intercept(FILES, {"audit": True}).use(reader)
    await runtime.settled()
    assert sorted(modes) == ["read-only", "read-write"]
    assert {"readonly": True, "root": "/srv", "audit": True} in served

async def test_metadata_for_a_key_that_is_not_required_is_refused() -> None:
    import pytest
    with pytest.raises(ValueError, match="declares metadata for files"):
        Component("confused", metadata={FILES: {}})
