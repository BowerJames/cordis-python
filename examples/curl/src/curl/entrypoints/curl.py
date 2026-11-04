# A small curl: `uv run curl-example https://example.com -i`.
import asyncio
import sys
from typing import Sequence

from cordis_loader import Entry
from cordis_plugins import UsageError, argparse_cli, get_env_os, httpx_http, run_application, stdio
from curl.components import Abandoned, curl, verbose


def compose(argv: Sequence[str] | None = None) -> list[Entry]:
    return [
        Entry("stdio", stdio),
        Entry("env", get_env_os),
        Entry("cli", argparse_cli, config={"argv": argv, "prog": "curl"}),
        Entry("http", httpx_http),
        Entry("curl", curl),
        Entry("verbose", verbose),
    ]

async def run(entries: list[Entry]) -> int:
    # The application reports what went wrong; turning that into an exit status is this deliverable's job.
    outcome = await run_application(entries)
    if outcome.failed_with(UsageError):
        return 2  # the parser has already written the usage
    if outcome.failed_with(Abandoned):
        sys.stderr.write("curl: interrupted\n")
        return 130  # as for any program ended by Ctrl-C
    for _, error in outcome.failures:
        notes = "; ".join(getattr(error, "__notes__", []))
        sys.stderr.write(f"curl: {error}" + (f" ({notes})" if notes else "") + "\n")
    return outcome.status

def main() -> None:
    sys.exit(asyncio.run(run(compose())))

if __name__ == "__main__":
    main()
