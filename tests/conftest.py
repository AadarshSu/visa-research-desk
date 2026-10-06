"""Suite-wide guards.

`AGENTS.md` requires that tests never touch the network or an LLM, and until 2026-08-22 nothing
enforced it — the rule was a convention, and the seams (`transport=`, `now=`, `renderer=`, the fake
generators) were what kept it true in practice. That works right up until a change adds a code path
with no seam.

It did. Teaching `visa-discover corridor` to fall back to the authority registry meant
`run_corridor` stopped returning early for `united-states`, and the existing test drove it into a
**real** corridor resolution: 21 seconds, live Brave searches, live page fetches and a live model
call, because `.env` is present on a developer machine and `settings` reads it. The test failed for
an unrelated-looking reason and the network access was invisible in the output.

So the rule now has teeth. Any attempt to open a socket during a test raises, naming the guard, and
a test that genuinely needs a transport must inject a fake one.
"""

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


class NetworkAccessDuringTest(RuntimeError):
    """Raised when a test tries to open a real connection.

    Not an `OSError`. Several code paths under test catch `OSError` and `httpx.HTTPError` and turn
    them into an ordinary "unreachable source" outcome — which is exactly the reporting this project
    cares about, and exactly what would swallow this guard and let the offending test pass while
    quietly describing the network block as an authority being down.
    """


@pytest.fixture(autouse=True)
def allowance_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Free-plan counts go to a fresh file per test, never to the real `var/allowance/`."""

    from visa_research_agent.config.settings import settings

    monkeypatch.setattr(settings, "allowance_file", tmp_path / "allowance" / "anonymous.json")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Block real connections for every test.

    Patched at `socket.socket.connect` rather than higher up on purpose: it is the one chokepoint
    every HTTP client, DNS resolver and driver ends up at, so a new dependency cannot route around
    it the way patching `httpx` would allow.
    """

    def refuse(self: socket.socket, address: Any) -> None:
        raise NetworkAccessDuringTest(
            f"a test tried to connect to {address!r}. Tests must not touch the network or an LLM "
            "(AGENTS.md); inject a fake transport, renderer or generator instead."
        )

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    yield
