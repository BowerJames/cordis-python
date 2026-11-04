from typing import Callable

from cordis import Key

# Writes text exactly as given: no newline is added.
type Write = Callable[[str], None]
# Whether a stream is shown to a person at a terminal, rather than piped or redirected somewhere.
type IsTerminal = Callable[[], bool]

STDOUT: Key[Write] = Key("stdout")
STDERR: Key[Write] = Key("stderr")
STDOUT_IS_TERMINAL: Key[IsTerminal] = Key("stdout_is_terminal")
STDERR_IS_TERMINAL: Key[IsTerminal] = Key("stderr_is_terminal")
