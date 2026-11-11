from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def clean_agent_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests must not pick up EDGE_AGENT_* variables from the developer's shell."""
    import os

    for name in list(os.environ):
        if name.startswith("EDGE_AGENT_"):
            monkeypatch.delenv(name)
    yield
