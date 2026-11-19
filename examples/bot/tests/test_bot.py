import pytest

from bot.entrypoints.bot import configuration, main, run, say
from cordis import Runtime
from cordis_loader import Loader


async def test_the_bot_follows_its_configuration_while_it_runs() -> None:
    assert await run() == [
        "bot: counting from here",
        "ada: hi",
        "bot: hello, ada",
        "ada: count",
        "bot: ada, that is message 2",
        # The counter is disabled, and the greeter takes its new greeting without reloading.
        "bot: no longer counting",
        "grace: hi",
        "bot: hey, grace",
        "grace: count",
        # The counter comes back; the store it counts in stayed, and saw nothing while it was gone.
        "bot: counting from here",
        "grace: count",
        "bot: grace, that is message 1",
        "bot: no longer counting",
    ]

async def test_a_new_greeting_keeps_the_greeter_it_is_given_to(runtime: Runtime) -> None:
    lines: list[str] = []
    loader = Loader(runtime.root)
    await loader.load(configuration(lines))
    await runtime.settled()
    greeter = loader.fiber("features", "greeter")
    await loader.load(configuration(lines, greeting="hey"))
    await runtime.settled()
    assert loader.fiber("features", "greeter") is greeter
    await say(runtime, "ada", "hi")
    assert lines[-1] == "bot: hey, ada"

async def test_the_bot_describes_itself_without_starting(runtime: Runtime) -> None:
    lines: list[str] = []
    description = Loader(runtime.root).describe(configuration(lines, counting=False))
    assert runtime.fibers() == ()
    assert description.splitlines()[:6] == [
        "Activation order (unloaded in reverse):",
        "  1. events",
        "  2. channel  after events (events)",
        "  3. features",
        "  4. greeter  after events (events), channel (channel)",
        "  5. store",
    ]
    assert "    counter  disabled" in description

async def test_disabling_the_store_parks_only_the_counter(runtime: Runtime) -> None:
    lines: list[str] = []
    loader = Loader(runtime.root)
    entries = configuration(lines)
    await loader.load(entries)
    await runtime.settled()
    await loader.load([entry for entry in entries if entry.id != "store"])
    await runtime.settled()
    await say(runtime, "ada", "hi")
    assert lines[-2:] == ["ada: hi", "bot: hello, ada"]
    assert "  - counter requires store, which nothing provides" in loader.describe()

def test_main_prints_the_description(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--describe"])
    assert capsys.readouterr().out.startswith("Activation order (unloaded in reverse):\n  1. events\n")
