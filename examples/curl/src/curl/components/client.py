import asyncio
from typing import Iterator

from cordis import Component, Context, Inverse
from cordis_plugins import (
    APPLICATION,
    ARGS,
    CLI,
    HTTP,
    STDERR,
    STDOUT,
    STDOUT_IS_TERMINAL,
    HTTPError,
    Request,
    Response,
    background,
    option,
    positional,
    repeated,
    switch,
)


class Abandoned(Exception):
    # The application was ended while the request was still waiting, so it was given up.
    pass

def seconds(text: str) -> float:
    # A time limit; argparse reports a ValueError here as an invalid seconds value.
    value = float(text)
    if value <= 0:
        raise ValueError(text)
    return value

def header(text: str) -> tuple[str, str]:
    # A header given as 'Name: value'; argparse reports a ValueError here as an invalid header value.
    name, separator, value = text.partition(":")
    if not separator or not name.strip():
        raise ValueError(text)
    return name.strip(), value.strip()

URL = positional("url", "the URL to request", str)
METHOD = option("method", ("-X", "--request"), "the HTTP method to use (default GET, or POST with --data)", str)
HEADERS = repeated("header", ("-H", "--header"), "a header to send, as 'Name: value'", header)
DATA = option("data", ("-d", "--data"), "data to send as the request body", str)
INCLUDE = switch("include", ("-i", "--include"), "include the status line and headers in the output")
MAX_TIME = option("max_time", ("-m", "--max-time"), "the most seconds to wait for the whole request", seconds)

def _curl_fetch(ctx: Context) -> Inverse:
    async def work(stopping: asyncio.Event) -> None:
        args = ctx.get(ARGS)
        data = args(DATA)
        method = args(METHOD) or ("POST" if data is not None else "GET")
        request = Request(method=method, url=args(URL), headers=args(HEADERS),
                          body=data.encode() if data is not None else b"", timeout=args(MAX_TIME))
        # Told to stop while still waiting, the request is given up rather than waited out.
        sending = asyncio.ensure_future(ctx.get(HTTP).send(request))
        ending = asyncio.ensure_future(stopping.wait())
        try:
            done, _ = await asyncio.wait({sending, ending}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ending.cancel()
            if not sending.done():
                sending.cancel()
        if sending not in done:
            raise Abandoned("the application ended before the response arrived")
        response = sending.result()
        write = ctx.get(STDOUT)
        if args(INCLUDE):
            write(f"HTTP/1.1 {response.status} {response.reason}\n")
            for name, value in response.headers:
                write(f"{name}: {value}\n")
            write("\n")
        body = response.text()
        write(body)
        # At a terminal, the prompt should start on a line of its own; piped or redirected, the body stays exact.
        if body and not body.endswith("\n") and ctx.get(STDOUT_IS_TERMINAL)():
            write("\n")
        # The one request is what the application exists for, so it is over once that is printed.
        ctx.get(APPLICATION).end(0)

    return background(ctx, "fetch", work)

def _curl(ctx: Context) -> Iterator[Inverse]:
    # Each argument is registered for as long as curl is loaded: registering gives back the inverse it yields.
    for argument in (URL, METHOD, HEADERS, DATA, INCLUDE, MAX_TIME):
        yield ctx.get(CLI).add(argument)
    yield ctx.inject([APPLICATION, ARGS, HTTP, STDOUT, STDOUT_IS_TERMINAL], _curl_fetch, name="fetch").retire

# Registers its arguments as it loads; the fetch is a child that requires the parsed arguments, so it activates
# once the command line has been parsed, and makes the one request the application exists for.
curl = Component("curl", _curl, requires=(CLI,))

VERBOSITY = switch("verbose", ("-v", "--verbose"), "show the request and response headers on stderr")

def _verbose_observe(ctx: Context) -> Iterator[Inverse]:
    if not ctx.get(ARGS)(VERBOSITY):
        return
    write = ctx.get(STDERR)

    def shown(request: Request, outcome: Response | HTTPError) -> None:
        if isinstance(outcome, HTTPError):
            write(f"* {request.method} {request.url} failed: {outcome}\n")
            return
        # What actually went out, headers the provider added included.
        write(f"> {request.method} {request.url}\n")
        for name, value in request.headers:
            write(f"> {name}: {value}\n")
        write(">\n")
        write(f"< HTTP/1.1 {outcome.status} {outcome.reason}\n")
        for name, value in outcome.headers:
            write(f"< {name}: {value}\n")
        write("<\n")

    yield ctx.get(HTTP).observe(shown)

def _verbose(ctx: Context) -> Iterator[Inverse]:
    yield ctx.get(CLI).add(VERBOSITY)
    yield ctx.inject([ARGS, HTTP, STDERR], _verbose_observe, name="verbose output").retire

# Shows every exchange the HTTP client makes, when -v is given, without whatever makes the requests knowing it is
# there. It observes rather than wraps: observing is a registration of its own, so it needs no place in any order,
# and it is withdrawn with the component.
verbose = Component("verbose", _verbose, requires=(CLI,))
