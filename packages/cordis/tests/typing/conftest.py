import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # Each YAML case runs a full mypy pass, which takes longer than the global timeout.
    for item in items:
        if item.path.suffix == ".yml":
            item.add_marker(pytest.mark.timeout(30))
            # Every typing case runs mypy, a tool outside the test's own process.
            item.add_marker(pytest.mark.external)
