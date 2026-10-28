"""Shared fixtures for unit tests.

Runtime-profile env vars select real backends at bootstrap. A value exported in
the developer shell or CI (e.g. ``AGR_PROFILE=offline``) would silently override
what an individual test sets up, so every unit test starts without them; tests
that need one set it with ``monkeypatch.setenv``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

_PROFILE_ENV = ("AGR_PROFILE", "AGR_USE_LIVE_STORES", "AGR_ALLOW_LLM")


@pytest.fixture(scope="session", autouse=True)
def _isolate_profile_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    # Session scope so module-scoped fixtures (e.g. API TestClients) see it too.
    with pytest.MonkeyPatch.context() as mp:
        for name in _PROFILE_ENV:
            mp.delenv(name, raising=False)
        # Runtime sinks (audit log, review queue, ingest tasks, uploaded docs)
        # go to a temp dir instead of data/processed in the working tree.
        mp.setenv("AGR_RUNTIME_DIR", str(tmp_path_factory.mktemp("agr_runtime")))
        yield
