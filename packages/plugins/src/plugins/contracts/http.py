from dataclasses import dataclass
from email.message import Message
from typing import Awaitable, Callable, Protocol

from cordis import Inverse, Key

# Every HTTP client keeps these promises: send makes one request and returns its response, whatever its status; a
# request that produced no response raises HTTPError (HTTPTimeout, HTTPConnectionError), never the library's own
# exceptions. Every exchange, answered or failed, is shown to each observer, with the request as it actually went out.
# Observing is a registration of its own for each observer, so observers come and go independently: watching
# requests needs no wrapper around the client, and no order among those watching.

# Header pairs in order; a name may appear more than once.
type Headers = tuple[tuple[str, str], ...]

@dataclass(slots=True, frozen=True)
class Request:
    method: str
    url: str
    headers: Headers = ()
    body: bytes = b""
    # Seconds to wait for the whole exchange; None means the provider's default.
    timeout: float | None = None

@dataclass(slots=True, frozen=True)
class Response:
    status: int
    reason: str
    headers: Headers
    body: bytes
    # The request as it actually went out, with whatever the provider added.
    sent: Request

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        return next((value for key, value in self.headers if key.lower() == wanted), None)

    def text(self) -> str:
        # Decoded with the charset the response declares, or UTF-8; undecodable bytes are replaced, never raised.
        content_type = Message()
        content_type["Content-Type"] = self.header("Content-Type") or "text/plain"
        charset = content_type.get_content_charset() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

class HTTPError(Exception):
    # The request did not produce a response; every provider raises these, whatever library it uses.
    pass

class HTTPTimeout(HTTPError):
    pass

class HTTPConnectionError(HTTPError):
    pass

# Shown each exchange: the request made, and its response or the error it failed with.
type Observer = Callable[[Request, Response | HTTPError], None]

class HTTPClient(Protocol):
    def send(self, request: Request, /) -> Awaitable[Response]: ...
    def observe(self, observer: Observer, /) -> Inverse: ...

HTTP: Key[HTTPClient] = Key("http")
