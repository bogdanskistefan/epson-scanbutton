"""Tests für die Kandidatensuche des Daemons (Scanner aus, DHCP-Wechsel)."""

from __future__ import annotations

from ds560 import daemon
from ds560.config import Config, ScanConfig, ScannerConfig, SessionConfig
from ds560.scan import ScanSettings


def make_config(host: str | None = None) -> Config:
    return Config(
        scanner=ScannerConfig(host=host, port=1865, device_id="ES00FA",
                              mac="aa:bb:cc:dd:ee:ff", subnet="192.0.2.0/24"),
        session=SessionConfig(keepalive_seconds=10, backoff_start_seconds=1,
                              backoff_max_seconds=60, response_timeout_seconds=30,
                              connect_timeout_seconds=3, offline_poll_seconds=2,
                              sweep_interval_seconds=30, dead_after_seconds=5),
        scan=ScanConfig(output_dir=".", settings=ScanSettings()),
    )


def test_finder_keeps_last_ip_while_scanner_is_off(monkeypatch):
    sweeps = []
    arp = {"ips": ["192.0.2.79"]}

    def fake_find(mac, subnet, exclude=()):
        sweeps.append(1)
        return [ip for ip in arp["ips"] if ip not in exclude]

    monkeypatch.setattr(daemon, "find_scanner", fake_find)
    monkeypatch.setattr(daemon, "lookup_arp", lambda mac: arp["ips"])
    clock = {"t": 1000.0}
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock["t"])

    finder = daemon.Finder(make_config())
    assert finder.candidates() == ["192.0.2.79"]  # aus dem ARP-Cache, ohne Sweep
    finder.connected("192.0.2.79")

    arp["ips"] = []  # Scanner aus: ARP-Eintrag weg, gemerkte IP bleibt
    assert finder.candidates() == ["192.0.2.79"]
    assert finder.sweep(["192.0.2.79"]) == []  # erster Sweep: nichts Neues
    clock["t"] += 2
    assert finder.sweep(["192.0.2.79"]) == []  # noch nicht fällig
    assert len(sweeps) == 1

    arp["ips"] = ["192.0.2.80"]  # neue DHCP-IP, beim nächsten Sweep gefunden
    clock["t"] += 30
    assert finder.sweep(["192.0.2.79"]) == ["192.0.2.80"]
    assert len(sweeps) == 2


def test_finder_fixed_host(monkeypatch):
    monkeypatch.setattr(daemon, "find_scanner", lambda *a, **k: 1 / 0)
    finder = daemon.Finder(make_config("192.0.2.5"))
    assert finder.candidates() == ["192.0.2.5"]
    assert finder.sweep(["192.0.2.5"]) == []


def test_find_scanner_returns_on_first_new_ip(monkeypatch):
    from ds560 import discovery

    calls = {"n": 0}

    def fake_arp(mac):
        calls["n"] += 1
        return ["192.0.2.79"] + (["192.0.2.80"] if calls["n"] >= 3 else [])

    monkeypatch.setattr(discovery, "sweep", lambda subnet: None)
    monkeypatch.setattr(discovery, "lookup_arp", fake_arp)
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    # Veraltete, schon probierte IP beendet die Suche nicht; die neue schon.
    assert discovery.find_scanner("aa:bb:cc:dd:ee:ff", "192.0.2.0/24",
                                  exclude=["192.0.2.79"]) == ["192.0.2.80"]
    assert calls["n"] == 3
