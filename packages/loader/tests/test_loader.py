import sys
import types

import pytest

from cordis import Component, Context, Fiber, FiberState, Inverse, Key, Metadata, Runtime
from cordis.testing import Failures
from cordis_loader import Entry, Loader, entries_from

STORAGE: Key[str] = Key("storage")
FILES: Key[str] = Key("files")

def state(fiber: Fiber | None) -> FiberState | None:
    return None if fiber is None else fiber.state

def storage(path: str = "app.db") -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(STORAGE, path)
    return Component("storage", apply=apply, provides=(STORAGE,))

class Log:
    # What the bots saw, by name, so a test can tell an entry that reloaded from one that did not.
    def __init__(self) -> None:
        self.lines: list[str] = []

    def bot(self, name: str = "bot") -> Component:
        def apply(ctx: Context) -> Inverse:
            self.lines.append(f"{name} up on {ctx.get(STORAGE)}")
            return lambda: self.lines.append(f"{name} down")
        return Component(name, apply=apply, requires=(STORAGE,))

def configuration(log: Log, path: str = "app.db", bot_disabled: bool = False) -> list[Entry]:
    return [
        Entry("bot", log.bot, disabled=bot_disabled),
        Entry("storage", storage, config={"path": path}),
    ]


async def test_loading_entries_needs_no_order(runtime: Runtime) -> None:
    log = Log()
    loader = Loader(runtime.root)
    await loader.load(configuration(log))
    await runtime.settled()
    assert log.lines == ["bot up on app.db"]
    assert state(loader.fiber("bot")) is FiberState.ACTIVE

async def test_changing_an_entrys_config_rebuilds_that_entry_alone(runtime: Runtime) -> None:
    log = Log()
    loader = Loader(runtime.root)
    await loader.load([*configuration(log), Entry("other", log.bot, config={"name": "other"})])
    await runtime.settled()
    other = loader.fiber("other")
    await loader.load([*configuration(log, path="new.db"), Entry("other", log.bot, config={"name": "other"})])
    await runtime.settled()
    # Both bots follow their dependency, which was rebuilt; the entry itself was untouched by the loader.
    assert log.lines[-4:].count("bot up on new.db") == 1
    assert loader.fiber("other") is other

async def test_disabling_an_entry_unloads_it_and_enabling_brings_back_a_fresh_fiber(runtime: Runtime) -> None:
    log = Log()
    loader = Loader(runtime.root)
    await loader.load(configuration(log))
    await runtime.settled()
    first = loader.fiber("bot")
    await loader.load(configuration(log, bot_disabled=True))
    assert loader.fiber("bot") is None
    assert first is not None and first.removed
    await loader.load(configuration(log))
    await runtime.settled()
    assert log.lines == ["bot up on app.db", "bot down", "bot up on app.db"]
    assert loader.fiber("bot") is not first

async def test_an_entry_left_out_of_the_configuration_is_unloaded(runtime: Runtime) -> None:
    log = Log()
    loader = Loader(runtime.root)
    await loader.load(configuration(log))
    await runtime.settled()
    await loader.load([Entry("storage", storage)])
    await runtime.settled()
    assert log.lines == ["bot up on app.db", "bot down"]
    assert [entry.id for entry in loader.entries] == ["storage"]

async def test_groups_reconcile_their_children_by_id(runtime: Runtime) -> None:
    log = Log()
    loader = Loader(runtime.root)
    await loader.load([Entry("storage", storage), Entry("bots", children=[Entry("a", log.bot, config={"name": "a"})])])
    await runtime.settled()
    group = loader.fiber("bots")
    a = loader.fiber("bots", "a")
    await loader.load([
        Entry("storage", storage),
        Entry("bots", children=[Entry("a", log.bot, config={"name": "a"}), Entry("b", log.bot, config={"name": "b"})]),
    ])
    await runtime.settled()
    assert loader.fiber("bots") is group
    assert loader.fiber("bots", "a") is a
    assert state(loader.fiber("bots", "b")) is FiberState.ACTIVE

async def test_disabling_a_group_unloads_its_children_and_enabling_restores_them(runtime: Runtime) -> None:
    log = Log()
    loader = Loader(runtime.root)
    bots = [Entry("a", log.bot, config={"name": "a"}), Entry("b", log.bot, config={"name": "b"})]
    await loader.load([Entry("storage", storage), Entry("bots", children=bots)])
    await runtime.settled()
    await loader.load([Entry("storage", storage), Entry("bots", children=bots, disabled=True)])
    await runtime.settled()
    assert sorted(log.lines[-2:]) == ["a down", "b down"]
    assert [fiber.name for fiber in runtime.fibers()] == ["storage"]
    await loader.load([Entry("storage", storage), Entry("bots", children=bots)])
    await runtime.settled()
    assert state(loader.fiber("bots", "b")) is FiberState.ACTIVE

async def test_isolation_gives_an_entry_its_own_realm_or_a_shared_named_one(runtime: Runtime) -> None:
    log = Log()
    loader = Loader(runtime.root)
    tenant = [Entry("storage", storage, config={"path": "tenant.db"}), Entry("bot", log.bot, config={"name": "t"})]
    await loader.load([
        Entry("storage", storage),
        Entry("bot", log.bot, config={"name": "public"}),
        Entry("tenant", children=tenant, isolate={STORAGE: True}),
        Entry("team-db", storage, config={"path": "team.db"}, isolate={STORAGE: "team"}),
        Entry("team-bot", log.bot, config={"name": "member"}, isolate={STORAGE: "team"}),
    ])
    await runtime.settled()
    assert sorted(log.lines) == ["member up on team.db", "public up on app.db", "t up on tenant.db"]

async def test_interception_is_updated_in_place_without_a_reload(runtime: Runtime) -> None:
    served: list[Metadata] = []
    loads: list[str] = []
    readers: list[Context] = []

    def files(ctx: Context) -> None:
        def serve(metadata: Metadata) -> str:
            served.append(metadata)
            return "read-only" if metadata.get("readonly") else "read-write"
        ctx.provide_by(FILES, serve)

    def reader() -> Component:
        def apply(ctx: Context) -> None:
            loads.append(ctx.get(FILES))
            readers.append(ctx)
        return Component("reader", apply=apply, requires=(FILES,))

    def files_component() -> Component:
        return Component("files", apply=files, provides=(FILES,))

    def config(readonly: bool) -> list[Entry]:
        return [
            Entry("files", files_component),
            Entry("reader", reader, intercept={FILES: {"readonly": readonly}}),
        ]

    loader = Loader(runtime.root)
    await loader.load(config(readonly=False))
    await runtime.settled()
    await loader.load(config(readonly=True))
    await runtime.settled()
    assert loads == ["read-write"]  # no reload
    assert readers[0].get(FILES) == "read-only"  # yet the next access reads the new metadata

async def test_a_bad_entry_fails_on_its_own_fiber(runtime: Runtime, failures: Failures) -> None:
    log = Log()
    loader = Loader(runtime.root)
    await loader.load([*configuration(log), Entry("broken", "no_such_module_here:factory")])
    await runtime.settled()
    assert state(loader.fiber("broken")) is FiberState.FAILED
    assert state(loader.fiber("bot")) is FiberState.ACTIVE
    assert failures.reported == [("broken", "No module named 'no_such_module_here'")]

async def test_reconfiguring_ends_where_loading_from_scratch_would(failures: Failures) -> None:
    # Theorem 80, through the loader: the quiescent state is a function of the final configuration alone.
    from cordis import open_runtime

    def snapshot(runtime: Runtime) -> set[tuple[str, FiberState]]:
        return {(fiber.name, fiber.state) for fiber in runtime.fibers()}

    def config(log: Log, step: int) -> list[Entry]:
        return [
            Entry("storage", storage, config={"path": f"db{step % 2}"}, disabled=step == 3),
            Entry("bots", children=[Entry(f"b{index}", log.bot, config={"name": f"b{index}"}) for index in range(step)]),
            Entry("lone", log.bot, config={"name": "lone"}, isolate={STORAGE: True}),
        ]

    final = 4
    async with open_runtime(on_error=failures) as runtime:
        log = Log()
        loader = Loader(runtime.root)
        for step in range(final + 1):
            await loader.load(config(log, step))
        await runtime.settled()
        reconfigured = snapshot(runtime)
    async with open_runtime(on_error=failures) as runtime:
        loader = Loader(runtime.root)
        await loader.load(config(Log(), final))
        await runtime.settled()
        assert snapshot(runtime) == reconfigured

def test_entries_are_read_from_plain_data(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("fake_components")
    module.storage = storage  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_components", module)
    (entry,) = entries_from(
        [{"id": "s", "component": "fake_components:storage", "config": {"path": "x.db"}, "isolate": {"storage": True}}],
        {"storage": STORAGE},
    )
    assert entry == Entry("s", "fake_components:storage", config={"path": "x.db"}, isolate={STORAGE: True})
    with pytest.raises(ValueError, match="names key 'files'"):
        entries_from([{"id": "s", "component": "m:f", "intercept": {"files": {}}}], {"storage": STORAGE})
    with pytest.raises(ValueError, match="unknown fields: name"):
        entries_from([{"id": "s", "name": "m:f"}], {})

def test_an_entry_names_a_component_or_children() -> None:
    with pytest.raises(ValueError, match="must name a component or children"):
        Entry("nothing")
    with pytest.raises(ValueError, match="more than one child called a"):
        Entry("group", children=[Entry("a", storage), Entry("a", storage)])
