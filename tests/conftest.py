"""Suite-wide guards.

The suite is meant to run with no network and no API keys. One thing quietly
broke that: ``Engine.start`` schedules ``refresh_all_models`` to warm the live
model cache, and ``connected_providers`` counts the keyless local services —
ollama and lmstudio — as connected without any auth at all. So every engine a
test started opened TCP connections to localhost:11434 and localhost:1234.

Usually those are refused immediately and nothing is visible. Occasionally the
attempt is still in flight when the loop closes, and the abandoned coroutine
surfaces later as::

    RuntimeWarning: coroutine 'connect_tcp.<locals>.try_connect' was never awaited

attributed by the garbage collector to whichever unrelated test happened to be
running. Worse, a developer running ollama locally had their tests querying it.

The warm-up is a real feature — it is why model pickers are not stuck on the
curated lists — so it stays in production and keeps its own coverage in
``tests/engine/test_providers.py``. It just has no business firing in tests.
"""

from __future__ import annotations

import pytest

from bytebarn.engine.facade import Engine

# grabbed before any patching, so the warm-up's own test can still reach it
_REAL_REFRESH_ALL_MODELS = Engine.refresh_all_models


@pytest.fixture(autouse=True)
def no_model_prefetch(monkeypatch):
    """Stop Engine.start's background model warm-up from touching the network.

    Patched on the class, so it covers engines a test constructs itself as well
    as those built by fixtures — which also means an instance cannot escape it.
    Use the ``real_refresh_all_models`` fixture to get the original.
    """

    async def _skip(self) -> None:
        return None

    monkeypatch.setattr(Engine, "refresh_all_models", _skip)


@pytest.fixture
def real_refresh_all_models():
    """The unpatched warm-up, for the one test that covers its behaviour."""
    return _REAL_REFRESH_ALL_MODELS
