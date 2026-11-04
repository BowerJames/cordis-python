import asyncio
from typing import AsyncIterator, Awaitable, Callable

import httpx
import requests

from cordis import Component, Context, Entries, Inverse

from ..contracts import (
    GET_ENV,
    HTTP,
    GetEnv,
    Headers,
    HTTPClient,
    HTTPConnectionError,
    HTTPError,
    HTTPTimeout,
    Observer,
    Request,
    Response,
)

# Every HTTP provider waits this long by default, unless HTTP_TIMEOUT says otherwise.
DEFAULT_HTTP_TIMEOUT = 30.0
HTTP_TIMEOUT_VARIABLE = "HTTP_TIMEOUT"

def default_timeout(get_env: GetEnv | None) -> float:
    configured = get_env(HTTP_TIMEOUT_VARIABLE) if get_env is not None else None
    if configured is None:
        return DEFAULT_HTTP_TIMEOUT
    try:
        seconds = float(configured)
    except ValueError:
        raise ValueError(f"{HTTP_TIMEOUT_VARIABLE} must be a number of seconds, not {configured!r}") from None
    if seconds <= 0:
        raise ValueError(f"{HTTP_TIMEOUT_VARIABLE} must be more than zero, not {configured!r}")
    return seconds

class _Observed:
    # A provider's own way of sending, shown to every observer after each exchange.
    __slots__ = ("_observers", "_send")

    def __init__(self, send: Callable[[Request], Awaitable[Response]]) -> None:
        self._send = send
        self._observers: Entries[Observer] = Entries()

    async def send(self, request: Request, /) -> Response:
        try:
            response = await self._send(request)
        except HTTPError as error:
            for observer in self._observers:
                observer(request, error)
            raise
        for observer in self._observers:
            observer(response.sent, response)
        return response

    def observe(self, observer: Observer, /) -> Inverse:
        return self._observers.add(observer)

def observed_client(send: Callable[[Request], Awaitable[Response]]) -> HTTPClient:
    # The value every provider binds at HTTP: send, with the observing the contract promises around it.
    client = _Observed(send)
    return HTTPClient(send=client.send, observe=client.observe)

def httpx_http() -> Component:
    # Sends requests with httpx's async client: bounded by a timeout, and cancelled the moment its caller is. The
    # client is opened as the component loads and closed as it unloads.
    async def apply(ctx: Context) -> AsyncIterator[Inverse]:
        default = default_timeout(ctx.get(GET_ENV))
        client = httpx.AsyncClient()
        yield client.aclose

        async def send(request: Request) -> Response:
            timeout = request.timeout if request.timeout is not None else default
            built = client.build_request(request.method, request.url, headers=list(request.headers),
                                         content=request.body or None, timeout=timeout)
            try:
                # httpx limits each phase on its own; the contract limits the whole exchange.
                async with asyncio.timeout(timeout):
                    answer = await client.send(built)
            except (TimeoutError, httpx.TimeoutException) as error:
                raise HTTPTimeout(str(error) or f"no response within {timeout}s") from error
            except httpx.TransportError as error:
                raise HTTPConnectionError(str(error) or type(error).__name__) from error
            except httpx.HTTPError as error:
                raise HTTPError(str(error) or type(error).__name__) from error
            sent = Request(method=built.method, url=str(built.url), headers=tuple(built.headers.multi_items()),
                           body=built.content, timeout=timeout)
            return Response(status=answer.status_code, reason=answer.reason_phrase,
                            headers=tuple(answer.headers.multi_items()), body=answer.content, sent=sent)

        ctx.provide(HTTP, observed_client(send))

    return Component("httpx http", apply=apply, requires=(GET_ENV,), provides=(HTTP,))

def _body(prepared: requests.PreparedRequest) -> bytes:
    body = prepared.body
    if body is None:
        return b""
    return body.encode() if isinstance(body, str) else bytes(body)

def http_requests() -> Component:
    # Sends requests with the requests library, in a thread: bounded by a timeout, but the thread cannot be cancelled.
    def apply(ctx: Context) -> Inverse:
        default = default_timeout(ctx.get(GET_ENV))
        session = requests.Session()

        def send_now(request: Request) -> Response:
            # requests takes headers as a mapping, so a repeated name is joined into one header.
            headers: dict[str, str] = {}
            for name, value in request.headers:
                headers[name] = f"{headers[name]}, {value}" if name in headers else value
            prepared = session.prepare_request(
                requests.Request(request.method, request.url, headers=headers, data=request.body or None)
            )
            timeout = request.timeout if request.timeout is not None else default
            try:
                answer = session.send(prepared, timeout=timeout)
            except requests.Timeout as error:
                raise HTTPTimeout(str(error)) from error
            except requests.ConnectionError as error:
                raise HTTPConnectionError(str(error)) from error
            except requests.RequestException as error:
                raise HTTPError(str(error)) from error
            sent = Request(method=prepared.method or request.method, url=prepared.url or request.url,
                           headers=tuple(prepared.headers.items()), body=_body(prepared), timeout=timeout)
            return Response(status=answer.status_code, reason=answer.reason, headers=tuple(answer.headers.items()),
                            body=answer.content, sent=sent)

        async def send(request: Request) -> Response:
            # The thread may run on until its own limits stop it, but the caller is released on time.
            timeout = request.timeout if request.timeout is not None else default
            try:
                async with asyncio.timeout(timeout):
                    return await asyncio.to_thread(send_now, request)
            except TimeoutError as error:
                raise HTTPTimeout(f"no response within {timeout}s") from error

        ctx.provide(HTTP, observed_client(send))
        return session.close

    return Component("http requests", apply=apply, requires=(GET_ENV,), provides=(HTTP,))

type Answer = Callable[[Request], Response] | HTTPError

def respond(status: int = 200, reason: str = "OK", headers: Headers = (), body: bytes = b"") -> Callable[[Request], Response]:
    # An answer for recording_http: the response it gives to any request, recording that request as sent.
    def answer(request: Request) -> Response:
        return Response(status=status, reason=reason, headers=headers, body=body, sent=request)
    return answer

def recording_http(answer: Answer) -> tuple[Callable[[], Component], list[Request]]:
    # A stand-in for an HTTP provider that keeps every request and answers without touching the network.
    sent: list[Request] = []

    async def send(made: Request) -> Response:
        sent.append(made)
        if isinstance(answer, HTTPError):
            raise answer
        return answer(made)

    def apply(ctx: Context) -> None:
        ctx.provide(HTTP, observed_client(send))

    return lambda: Component("recording http", apply=apply, provides=(HTTP,)), sent
