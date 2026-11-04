import secrets
import sys
from pathlib import Path
from typing import Iterator

import pytest

from cordis import FiberState, Runtime
from cordis_loader import Entry, Loader, RestartRequired, classify, source_imports


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Path]]:
    # A throwaway package on sys.path, compiled from source on every import, and forgotten afterwards.
    name = f"hot_{secrets.token_hex(4)}"
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "keys.py").write_text('from cordis import Key\nGREETING: Key[str] = Key("greeting")\n')
    (root / "text.py").write_text('TEXT = "hello"\n')
    (root / "greeter.py").write_text(
        "from cordis import Component, Context\n"
        "from .keys import GREETING\n"
        f"from {name}.text import TEXT\n"
        "def greeter() -> Component:\n"
        "    def apply(ctx: Context) -> None:\n"
        "        ctx.provide(GREETING, TEXT)\n"
        '    return Component("greeter", apply=apply, provides=(GREETING,))\n'
    )
    (root / "listener.py").write_text(
        "from cordis import Component, Context\n"
        "from . import keys\n"
        "def listener(log: list[str]) -> Component:\n"
        "    def apply(ctx: Context) -> None:\n"
        "        log.append(ctx.get(keys.GREETING))\n"
        '    return Component("listener", apply=apply, requires=(keys.GREETING,))\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    yield name, root
    for module in [module for module in sys.modules if module == name or module.startswith(f"{name}.")]:
        del sys.modules[module]


def test_a_module_is_accepted_once_one_of_its_imports_is() -> None:
    graph = {"app": {"lib", "util"}, "lib": {"util"}, "util": set(), "vendor": set(), "cycle_a": {"cycle_b"},
             "cycle_b": {"cycle_a", "lib"}}

    def imports_of(name: str) -> set[str]:
        return graph[name]

    accepted, declined = classify({"util"}, set(), imports_of)
    assert accepted == {"util"}
    accepted, declined = classify({"app"}, set(), imports_of)
    assert accepted == {"app"} and declined == {"lib", "util"}
    accepted, declined = classify({"cycle_a"}, {"lib"}, imports_of)
    assert accepted == {"cycle_a", "cycle_b"}

def test_source_imports_reads_absolute_and_relative_imports_under_the_roots(package: tuple[str, Path]) -> None:
    name, _ = package
    imports_of = source_imports([name])
    assert set(imports_of(f"{name}.greeter")) == {f"{name}.keys", f"{name}.text"}
    assert set(imports_of(f"{name}.listener")) == {name, f"{name}.keys"}

async def test_a_change_reloads_the_entries_that_reach_it_and_nothing_else(
    runtime: Runtime, package: tuple[str, Path],
) -> None:
    name, root = package
    log: list[str] = []
    loader = Loader(runtime.root)
    await loader.load([
        Entry("listener", f"{name}.listener:listener", config={"log": log}),
        Entry("greeter", f"{name}.greeter:greeter"),
    ])
    await runtime.settled()
    keys = sys.modules[f"{name}.keys"]
    listener = loader.fiber("listener")
    (root / "text.py").write_text('TEXT = "hi"\n')
    reloaded = await loader.reload([f"{name}.text"], source_imports([name]))
    await runtime.settled()
    assert reloaded == [("greeter",)]
    # The listener's own module was untouched; it reactivated because its provider is a new fiber.
    assert loader.fiber("listener") is listener
    assert log == ["hello", "hi"]
    # The keys module was not invalidated, so every key keeps its identity across the reload.
    assert sys.modules[f"{name}.keys"] is keys

async def test_a_module_that_fails_to_import_rolls_the_reload_back(runtime: Runtime, package: tuple[str, Path]) -> None:
    name, root = package
    log: list[str] = []
    loader = Loader(runtime.root)
    await loader.load([
        Entry("listener", f"{name}.listener:listener", config={"log": log}),
        Entry("greeter", f"{name}.greeter:greeter"),
    ])
    await runtime.settled()
    greeter = loader.fiber("greeter")
    text = sys.modules[f"{name}.text"]
    (root / "text.py").write_text("TEXT = \n")
    with pytest.raises(SyntaxError):
        await loader.reload([f"{name}.text"], source_imports([name]))
    await runtime.settled()
    assert loader.fiber("greeter") is greeter
    assert greeter is not None and greeter.state is FiberState.ACTIVE
    assert sys.modules[f"{name}.text"] is text
    assert log == ["hello"]

async def test_a_change_to_an_external_module_needs_a_restart(runtime: Runtime, package: tuple[str, Path]) -> None:
    name, _ = package
    loader = Loader(runtime.root)
    with pytest.raises(RestartRequired, match="keys cannot be hot-replaced"):
        await loader.reload([f"{name}.keys"], source_imports([name]), externals=[f"{name}.keys"])
