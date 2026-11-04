import asyncio
import dataclasses
import os
import signal

import pytest

from cordis import Component, Context
from cordis_loader import Entry, patched
from cordis_plugins import (
    HTTP,
    Answer,
    Captured,
    HTTPConnectionError,
    Request,
    Response,
    capturing_stdio,
    describe,
    observed_client,
    recording_http,
    respond,
)
from curl.components import curl as curl_component
from curl.entrypoints.curl import compose, run


async def curl(argv: list[str], answer: Answer, terminal: bool = False) -> tuple[int, Captured, list[Request]]:
    # The composition as shipped, with the HTTP client and stdio swapped by entry for ones that record.
    fake, sent = recording_http(answer)
    capture, captured = capturing_stdio(terminal=terminal)
    return await run(patched(compose(argv), Entry("http", fake), Entry("stdio", capture))), captured, sent

OK = respond(200, "OK", (("Content-Type", "text/plain"),), b"hello\n")


async def test_a_get_prints_the_body() -> None:
    status, captured, sent = await curl(["https://example.com"], OK)
    assert status == 0
    assert captured.stdout == "hello\n"
    [request] = sent
    assert request == Request(method="GET", url="https://example.com")

async def test_method_headers_and_data_are_sent() -> None:
    status, _, sent = await curl(["https://example.com", "-X", "PUT", "-H", "Accept: text/plain",
                                  "-H", "X-Trace: 1", "-d", "body"], OK)
    assert status == 0
    [request] = sent
    assert request.method == "PUT"
    assert request.headers == (("Accept", "text/plain"), ("X-Trace", "1"))
    assert request.body == b"body"

async def test_data_without_a_method_is_posted() -> None:
    _, _, [request] = await curl(["https://example.com", "-d", "body"], OK)
    assert request.method == "POST"

async def test_include_prints_the_status_line_and_headers_first() -> None:
    _, captured, _ = await curl(["https://example.com", "-i"], OK)
    assert captured.stdout == "HTTP/1.1 200 OK\nContent-Type: text/plain\n\nhello\n"

async def test_an_error_status_is_still_printed_and_succeeds() -> None:
    status, captured, _ = await curl(["https://example.com/missing"], respond(404, "Not Found", body=b"nothing here"))
    assert status == 0
    assert captured.stdout == "nothing here"

async def test_help_is_printed_and_no_request_is_made() -> None:
    status, captured, sent = await curl(["--help"], OK)
    assert status == 0
    assert captured.stdout.startswith("usage: curl")
    assert "include the status line and headers in the output" in captured.stdout
    assert sent == []

async def test_a_missing_url_is_a_usage_error() -> None:
    status, captured, sent = await curl([], OK)
    assert status == 2
    assert "curl: error: the following arguments are required: url" in captured.stderr
    assert sent == []

async def test_a_malformed_header_is_a_usage_error() -> None:
    status, captured, sent = await curl(["https://example.com", "-H", "no-colon"], OK)
    assert status == 2
    assert "invalid header value: 'no-colon'" in captured.stderr
    assert sent == []

async def test_a_failed_request_is_reported_on_stderr_and_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    status, captured, _ = await curl(["https://example.com"], HTTPConnectionError("connection refused"))
    assert status == 1
    assert captured.stdout == ""
    assert capsys.readouterr().err == "curl: connection refused (in fetch)\n"

def test_the_composition_is_described_without_running_anything() -> None:
    # The order a load from scratch activates the entries in, and why; curl's fetch and verbose's output are children
    # each instantiates once the command line is parsed, so they appear only once it runs.
    assert describe(compose(["https://example.com"])) == """\
Activation order (unloaded in reverse):
  1. application
  2. stdio
  3. env
  4. cli         after application (application), stdio (stdout, stderr)
  5. http        after env (get_env)
  6. curl        after cli (cli)
  7. verbose     after cli (cli)

Entries:
  application  application
  stdio        stdio
  env          env os
  cli          argparse cli
  http         httpx http
  curl         curl
  verbose      verbose

Keys:
  application         provided by application; required by cli
  stdout              provided by stdio; required by cli
  stderr              provided by stdio; required by cli
  stdout_is_terminal  provided by stdio
  stderr_is_terminal  provided by stdio
  get_env             provided by env; required by http
  cli                 provided by cli; required by curl, verbose
  args                provided by cli
  http                provided by http"""


# curl -v: added by a component of its own, without editing curl

async def test_without_verbose_nothing_is_written_to_stderr() -> None:
    _, captured, _ = await curl(["https://example.com", "-H", "X-Demo: hello"], OK)
    assert captured.stderr == ""
    assert captured.stdout == "hello\n"

async def test_verbose_shows_the_request_and_response_on_stderr_and_leaves_stdout_alone() -> None:
    status, captured, _ = await curl(["https://example.com", "-v", "-H", "X-Demo: hello"], OK)
    assert status == 0
    assert captured.stderr == (
        "> GET https://example.com\n"
        "> X-Demo: hello\n"
        ">\n"
        "< HTTP/1.1 200 OK\n"
        "< Content-Type: text/plain\n"
        "<\n"
    )
    assert captured.stdout == "hello\n"

async def test_verbose_passes_the_request_and_response_through_unchanged() -> None:
    plain_status, plain, [plain_request] = await curl(["https://example.com", "-X", "PUT", "-d", "body", "-i"], OK)
    verbose_status, verbose, [verbose_request] = await curl(["https://example.com", "-X", "PUT", "-d", "body", "-i", "-v"], OK)
    assert verbose_status == plain_status == 0
    assert verbose.stdout == plain.stdout
    assert verbose_request == plain_request

async def test_verbose_appears_in_help() -> None:
    _, captured, _ = await curl(["--help"], OK)
    assert "-v, --verbose" in captured.stdout
    assert "show the request and response headers on stderr" in captured.stdout

def test_curl_itself_knows_nothing_of_verbose() -> None:
    # curl requires the command line alone; its fetch, a child, requires what the request needs. Nothing in either
    # names verbose, which only observes the HTTP client.
    assert {key.name for key in curl_component().requires} == {"cli"}


# -m/--max-time, and ending promptly

async def test_max_time_becomes_the_requests_timeout() -> None:
    _, _, [limited] = await curl(["https://example.com", "-m", "2.5"], OK)
    _, _, [unlimited] = await curl(["https://example.com"], OK)
    assert limited.timeout == 2.5
    assert unlimited.timeout is None

@pytest.mark.parametrize("given", ["soon", "0", "-1"])
async def test_max_time_must_be_a_positive_number_of_seconds(given: str) -> None:
    status, captured, sent = await curl(["https://example.com", "-m", given], OK)
    assert status == 2
    assert f"invalid seconds value: '{given}'" in captured.stderr
    assert sent == []

def never_answers() -> Component:
    async def request(made: Request) -> Response:
        await asyncio.Event().wait()
        raise AssertionError("never answered")

    def apply(ctx: Context) -> None:
        ctx.provide(HTTP, observed_client(request))
    return Component("never answers", apply=apply, provides=(HTTP,))

async def test_ctrl_c_gives_up_the_request_at_once_and_exits_130(capsys: pytest.CaptureFixture[str]) -> None:
    capture, captured = capturing_stdio()
    entries = patched(compose(["https://example.com"]), Entry("http", never_answers), Entry("stdio", capture))
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, os.kill, os.getpid(), signal.SIGINT)
    started = loop.time()
    status = await run(entries)
    assert loop.time() - started < 0.5
    assert status == 130
    assert capsys.readouterr().err == "curl: interrupted\n"
    assert captured.stdout == ""


# verbose shows what was actually sent

def answered_by_a_provider_that_adds_headers(request: Request) -> Response:
    sent = dataclasses.replace(request, url=request.url + "/", headers=(*request.headers, ("User-Agent", "provider/1.0")))
    return Response(status=200, reason="OK", headers=(), body=b"ok", sent=sent)

async def test_verbose_shows_the_request_as_the_provider_sent_it() -> None:
    _, captured, _ = await curl(["https://example.com", "-v", "-H", "X-Demo: hello"], answered_by_a_provider_that_adds_headers)
    assert captured.stderr.startswith(
        "> GET https://example.com/\n"
        "> X-Demo: hello\n"
        "> User-Agent: provider/1.0\n"
        ">\n"
    )

async def test_verbose_reports_a_failed_request() -> None:
    status, captured, _ = await curl(["https://example.com", "-v"], HTTPConnectionError("connection refused"))
    assert status == 1
    assert captured.stderr == "* GET https://example.com failed: connection refused\n"


# Ending the output with a newline at a terminal, and only there

async def test_at_a_terminal_a_body_without_a_final_newline_gets_one() -> None:
    _, captured, _ = await curl(["https://example.com"], respond(body=b"no newline"), terminal=True)
    assert captured.stdout == "no newline\n"

async def test_at_a_terminal_a_body_ending_in_a_newline_gets_no_second_one() -> None:
    _, captured, _ = await curl(["https://example.com"], respond(body=b"has one\n"), terminal=True)
    assert captured.stdout == "has one\n"

async def test_at_a_terminal_an_empty_body_prints_nothing() -> None:
    _, captured, _ = await curl(["https://example.com"], respond(body=b""), terminal=True)
    assert captured.stdout == ""

async def test_piped_or_redirected_the_body_is_exactly_what_was_received() -> None:
    _, captured, _ = await curl(["https://example.com"], respond(body=b"no newline"), terminal=False)
    assert captured.stdout == "no newline"
