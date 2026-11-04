from cordis import Fiber, FiberState


def state(fiber: Fiber) -> FiberState:
    # Read through a call, so mypy does not narrow fiber.state across the awaits that change it.
    return fiber.state
