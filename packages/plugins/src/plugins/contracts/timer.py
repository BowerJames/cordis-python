from dataclasses import dataclass
from typing import Callable

from cordis import Inverse, Key

# Every timer keeps these promises: callbacks run later on the running loop, and each scheduling returns the inverse
# that cancels it, so a timer set as an effect never fires after its fiber has unloaded. Each scheduling is an entry
# of its own, so cancelling one leaves every other in place, even one scheduling the same callback.

@dataclass(slots=True, frozen=True)
class Timer:
    # Runs callback once, delay seconds from now.
    after: Callable[[float, Callable[[], None]], Inverse]
    # Runs callback every interval seconds, until the inverse is applied.
    every: Callable[[float, Callable[[], None]], Inverse]
    # How many callbacks are scheduled and not yet run or cancelled.
    pending: Callable[[], int]

TIMER: Key[Timer] = Key("timer")
