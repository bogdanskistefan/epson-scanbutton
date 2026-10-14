"""Laden der lokalen Konfiguration (config.toml).

Alle Scan-Einstellungen stehen in der config.toml — bewusst ohne Abhängigkeit
von Epson-Scan-Dateien (``0000.ssf``, Registry). Erlaubte Werte entsprechen der
``CAPA``-Antwort des DS-560 (siehe docs/protocol.md, „Capabilities").
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .scan import ScanSettings

COLOR_MODES = {"color": "C024", "gray": "M008", "mono": "M001"}
RESOLUTIONS = (300, 600)
DOUBLE_FEED = {0: "", 1: "DFL1", 2: "DFL2"}


class ConfigError(ValueError):
    """Ungültiger Wert in der config.toml."""


@dataclass
class ScannerConfig:
    host: str | None
    port: int
    device_id: str
    mac: str | None
    subnet: str | None


@dataclass
class SessionConfig:
    keepalive_seconds: int
    backoff_start_seconds: int
    backoff_max_seconds: int
    response_timeout_seconds: int


@dataclass
class ScanConfig:
    output_dir: Path
    settings: ScanSettings


@dataclass
class Config:
    scanner: ScannerConfig
    session: SessionConfig
    scan: ScanConfig


def _choice(sc: dict, key: str, default, allowed):
    value = sc.get(key, default)
    if value not in allowed:
        raise ConfigError(f"[scan] {key} = {value!r} — erlaubt: {', '.join(map(repr, allowed))}")
    return value


def scan_settings(sc: dict) -> ScanSettings:
    """Übersetzt die [scan]-Sektion in ``ScanSettings`` und prüft die Werte."""
    quality = sc.get("jpeg_quality", 85)
    if not (isinstance(quality, int) and 1 <= quality <= 100):
        raise ConfigError(f"[scan] jpeg_quality = {quality!r} — erlaubt: 1–100")
    threshold = sc.get("threshold", 110)
    if not (isinstance(threshold, int) and 0 <= threshold <= 255):
        raise ConfigError(f"[scan] threshold = {threshold!r} — erlaubt: 0–255")
    for key in ("duplex", "overscan"):
        if not isinstance(sc.get(key, True), bool):
            raise ConfigError(f"[scan] {key} = {sc[key]!r} — erlaubt: true / false")
    return ScanSettings(
        duplex=sc.get("duplex", True),
        overscan=sc.get("overscan", True),
        threshold=threshold,
        double_feed=DOUBLE_FEED[_choice(sc, "double_feed", 1, tuple(DOUBLE_FEED))],
        color=COLOR_MODES[_choice(sc, "color", "color", tuple(COLOR_MODES))],
        jpeg_quality=quality,
        resolution=_choice(sc, "resolution", 300, RESOLUTIONS),
    )


def load_config(path: str | Path) -> Config:
    """Liest die config.toml ein und baut ein ``Config``-Objekt."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    s = data["scanner"]
    se = data["session"]
    sc = data["scan"]
    return Config(
        scanner=ScannerConfig(
            host=s.get("host") or None,
            port=int(s.get("port", 1865)),
            device_id=s["device_id"],
            mac=s.get("mac") or None,
            subnet=s.get("subnet") or None,
        ),
        session=SessionConfig(
            keepalive_seconds=int(se.get("keepalive_seconds", 60)),
            backoff_start_seconds=int(se.get("backoff_start_seconds", 1)),
            backoff_max_seconds=int(se.get("backoff_max_seconds", 60)),
            response_timeout_seconds=int(se.get("response_timeout_seconds", 30)),
        ),
        scan=ScanConfig(
            output_dir=Path(sc["output_dir"]).expanduser(),
            settings=scan_settings(sc),
        ),
    )
