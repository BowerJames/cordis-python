from .runtime import Fiber


class Failures:
    # An error handler for a runtime under test: collects what the runtime reports, in place of logging it, so a test
    # can say exactly what it expects to fail.
    def __init__(self) -> None:
        self.reported: list[tuple[str, str]] = []

    def __call__(self, fiber: Fiber, error: Exception) -> None:
        self.reported.append((fiber.name, str(error)))
