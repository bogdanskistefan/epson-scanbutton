"""Tests für ESC/I-2-Transaktionen in ``Link``."""

from __future__ import annotations

import pytest

from ds560 import link as link_mod
from ds560 import protocol as p
from ds560.link import Link, ProtocolError


def busy_then_ok(busy_count: int):
    """Wie im Log nach schnellem Reconnect: erst #nrdBUSY, dann normale Antwort."""
    calls = {"n": 0}

    def once(self, cmd, params):
        calls["n"] += 1
        if calls["n"] <= busy_count:
            return p.Reply(cmd, 0, b"#nrdBUSY"), b""
        return p.Reply(cmd, 4, b"#nrdNONE"), b"#PRD"

    return once, calls


def test_esci2_retries_while_busy(monkeypatch):
    once, calls = busy_then_ok(3)
    monkeypatch.setattr(Link, "_esci2_once", once)
    monkeypatch.setattr(link_mod.time, "sleep", lambda s: None)
    reply, data = Link(None).esci2(p.CMD_INFO)
    assert (reply.tokens, data, calls["n"]) == (b"#nrdNONE", b"#PRD", 4)


def test_esci2_gives_up_when_busy_too_long(monkeypatch):
    once, _ = busy_then_ok(10**6)
    monkeypatch.setattr(Link, "_esci2_once", once)
    clock = {"t": 0.0}
    monkeypatch.setattr(link_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(link_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    with pytest.raises(ProtocolError, match="BUSY"):
        Link(None).esci2(p.CMD_INFO)
