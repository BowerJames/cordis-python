# A small curl: `uv run curl-example https://example.com -i`.
import asyncio
import sys
from typing import Sequence

from cordis import open_runtime
from cordis_loader import Entry, Loader, without
from cordis_plugins import (
    StopOnFailure,
    UsageError,
    argparse_args,
    argument_table,
    get_env_os,
    httpx_http,
    stdio,
    stop_on_signals,
)
from curl.components import Abandoned, curl, verbose

# The entry that parses the command line, loaded after every other.
ARGS_ENTRY = "args"

def compose(argv: Sequence[str] | None = None) -> list[Entry]:
    return [
        Entry("stdio", stdio),
        Entry("env", get_env_os),
        Entry("cli", argument_table),
        Entry("http", httpx_http),
        Entry("curl", curl),
        Entry("verbose", verbose),
        Entry(ARGS_ENTRY, argparse_args, config={"argv": argv, "prog": "curl"}),
    ]

async def run(entries: list[Entry]) -> int:
    failed = StopOnFailure()
    async with open_runtime(on_error=failed) as runtime:
        with stop_on_signals(runtime):
            loader = Loader(runtime.root)
            # A command line is parsed whole, so its parser is loaded once everything else has settled, when every
            # component that will register an argument has: the one order this composition has to arrange.
            await loader.load(without(entries, ARGS_ENTRY))
            await runtime.settled()
            await loader.load(entries)
            # Done once the request has been made, since nothing else holds the runtime; or at once, when help was
            # asked for or the command line did not match, since then nothing waiting for the arguments ever starts.
            await runtime.done()
    # The runtime reports what went wrong; turning that into an exit status is this deliverable's job.
    if failed.of(UsageError):
        return 2  # the parser has already written the usage
    if failed.of(Abandoned):
        sys.stderr.write("curl: interrupted\n")
        return 130  # as for any program ended by Ctrl-C
    for _, error in failed.failures:
        notes = "; ".join(getattr(error, "__notes__", []))
        sys.stderr.write(f"curl: {error}" + (f" ({notes})" if notes else "") + "\n")
    return failed.status()

def main() -> None:
    sys.exit(asyncio.run(run(compose())))

if __name__ == "__main__":
    main()
