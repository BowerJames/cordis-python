from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, overload

from cordis import Inverse, Key

# A command line assembled from what the running components register, then parsed once the application has started.
# Registering an argument is an effect of the component registering it, so a component that is not loaded has no
# arguments; reading values needs ARGS, which is provided only once the command line has been parsed, so a component
# that reads them waits for that, as for any other key.

type Kind = Literal["positional", "option", "repeated", "switch"]

@dataclass(slots=True, frozen=True, eq=False)
class Argument[T]:
    # One command-line argument. Compared by identity, so it is the key to its own value; T is the value read back.
    name: str
    kind: Kind
    flags: tuple[str, ...]
    help: str
    parse: Callable[[str], Any]
    default: Any = None

    def __str__(self) -> str:
        return ", ".join(self.flags) if self.flags else self.name

def positional[T](name: str, help: str, parse: Callable[[str], T]) -> Argument[T]:
    return Argument(name=name, kind="positional", flags=(), help=help, parse=parse)

@overload
def option[T](name: str, flags: tuple[str, ...], help: str, parse: Callable[[str], T]) -> Argument[T | None]: ...
@overload
def option[T](name: str, flags: tuple[str, ...], help: str, parse: Callable[[str], T], default: T) -> Argument[T]: ...
def option[T](name: str, flags: tuple[str, ...], help: str, parse: Callable[[str], T], default: T | None = None) -> Argument[Any]:
    return Argument(name=name, kind="option", flags=flags, help=help, parse=parse, default=default)

def repeated[T](name: str, flags: tuple[str, ...], help: str, parse: Callable[[str], T]) -> Argument[tuple[T, ...]]:
    return Argument(name=name, kind="repeated", flags=flags, help=help, parse=parse)

def switch(name: str, flags: tuple[str, ...], help: str) -> Argument[bool]:
    return Argument(name=name, kind="switch", flags=flags, help=help, parse=str)

class Arguments(Protocol):
    # The value bound at CLI: add registers an argument until the inverse it returns is applied. Each registration is
    # an entry of its own, so registrations from different components are independent of each other.
    def add(self, argument: Argument[Any], /) -> Inverse: ...

class ReadArgument(Protocol):
    # Looks up the value of an argument: args(URL) -> str.
    def __call__[T](self, argument: Argument[T], /) -> T: ...

class UsageError(Exception):
    # The command line did not match the registered arguments; whichever parser found it raises this.
    pass

CLI: Key[Arguments] = Key("cli")
ARGS: Key[ReadArgument] = Key("args")
