from dataclasses import dataclass
from typing import Callable

from cordis import Key, Undo

# Every timer keeps these promises: callbacks run later on the running loop, and each scheduling returns the inverse
# that cancels it, so a timer whose inverse its fiber yielded never fires after the fiber has unloaded. Each scheduling is an entry
# of its own, so cancelling one leaves every other in place, even one scheduling the same callback.

@dataclass(slots=True, frozen=True)
class Timer:
    # Runs callback once, delay seconds from now.
    after: Callable[[float, Callable[[], None]], Undo]
    # Runs callback every interval seconds, until the inverse is applied.
    every: Callable[[float, Callable[[], None]], Undo]
    # How many callbacks are scheduled and not yet run or cancelled.
    pending: Callable[[], int]

TIMER: Key[Timer] = Key("timer")
