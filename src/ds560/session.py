"""Sitzungs-Zustandsautomat für eine einzelne TCP-Verbindung zum DS-560.

Ablauf (siehe docs/protocol.md):

    CONNECT -> Begrüßung 0x8000 -> Lock 0x2100 -> FS X -> INFO/CAPA/STAT
            -> IDLE (Keepalive 0x1000 alle keepalive_seconds)
            -> bei Benachrichtigung 0x9000/01: Scan, danach wieder IDLE
    0x9000/80 (Sitzung läuft ab), TCP FIN, Fehler, Timeout -> CLOSE
"""

from __future__ import annotations

import logging
import select
import socket
import time
from datetime import datetime
from pathlib import Path

from . import protocol as p
from .config import Config
from .link import Link, mask_serial
from .pdf import write_pdf
from .scan import ScanError, run_scan

log = logging.getLogger(__name__)


class SessionEnded(Exception):
    """Der Scanner hat die Sitzung beendet (0x9000/80 oder TCP FIN)."""


class Session:
    """Eine Verbindung zum Scanner, mit Handshake, Keepalive und Scan-Lauf."""

    def __init__(self, config: Config, host: str, trace: bool = False) -> None:
        self.config = config
        self.host = host
        self.trace = trace
        self.sock: socket.socket | None = None
        self.link: Link | None = None
        self.locked = False

    def connect(self) -> None:
        """TCP 1865 öffnen, Begrüßung abwarten, Lock + Handshake senden."""
        timeout = self.config.session.response_timeout_seconds
        self.sock = socket.create_connection((self.host, self.config.scanner.port), timeout=timeout)
        self.link = Link(self.sock, trace=self.trace)
        self.link.open()
        self.locked = True
        self.link.mode(p.FS_X)
        _, info = self.link.esci2(p.CMD_INFO)
        self.link.esci2(p.CMD_CAPA)
        _, stat = self.link.esci2(p.CMD_STAT)
        log.info("Sitzung offen mit %s: %s, Status %s", self.host,
                 _token(info, b"PRD"), stat.strip(b"\x00 ").decode(errors="replace") or "OK")
        log.debug("INFO %s", mask_serial(info))

    def run_until_disconnect(self) -> None:
        """Keepalive senden, auf den Knopf warten, bei Druck scannen.

        Kehrt nur über eine Ausnahme zurück (SessionEnded, ConnectionError,
        Timeout); die äußere Schleife baut dann neu auf.
        """
        assert self.link and self.sock
        interval = self.config.session.keepalive_seconds
        next_keepalive = time.monotonic() + interval
        while True:
            for value in self.link.pop_notifications():
                self._on_notification(value)
            wait = max(0.0, next_keepalive - time.monotonic())
            readable, _, _ = select.select([self.sock], [], [], wait)
            if readable:
                frame = self.link.recv_frame()
                if frame.type == p.TYPE_NOTIFY:
                    self._on_notification(frame.payload[0] if frame.payload else -1)
                else:
                    log.warning("Unerwarteter Rahmen im Leerlauf: %#06x %s",
                            frame.type, frame.payload[:32].hex(" "))
            if time.monotonic() >= next_keepalive:
                self.link.keepalive()
                log.debug("Keepalive gesendet")
                next_keepalive = time.monotonic() + interval

    def _on_notification(self, value: int) -> None:
        if value == p.NOTIFY_BUTTON:
            log.info("Scan-Knopf gedrückt")
            self.perform_scan()
        elif value == p.NOTIFY_SESSION_END:
            raise SessionEnded("Scanner beendet die Sitzung (0x9000/80)")
        else:
            log.warning("Unbekannte Benachrichtigung 0x9000/%02x", value & 0xFF)

    def perform_scan(self) -> None:
        """Einen Scan ausführen und als ein PDF im Zielordner ablegen.

        Scanner-Fehler (Stau, Doppeleinzug) beenden nur diesen Scan, nicht die
        Sitzung; Protokoll- und Verbindungsfehler gehen nach oben (Reconnect).
        """
        assert self.link
        settings = self.config.scan.settings
        started = time.monotonic()
        try:
            pages = run_scan(self.link, settings)
        except ScanError as exc:
            log.error("Scan abgebrochen: %s", exc)
            return
        if not pages:
            log.warning("Scan ohne Seiten (Einzug leer?)")
            return
        target = save_pdf(Path(self.config.scan.output_dir), pages, settings.resolution)
        log.info("Scan fertig: %d Seiten in %.1f s -> %s", len(pages),
                 time.monotonic() - started, target)

    def close(self) -> None:
        """Sitzung freigeben (FIN, Unlock) und Socket schließen; nie werfen."""
        if self.link and self.locked and self.sock:
            try:
                self.sock.settimeout(2)
                self.link.esci2(p.CMD_FIN)
                self.link.unlock()
            except Exception as exc:  # noqa: BLE001 — Abbau ist best effort
                log.debug("Abbau ohne saubere Freigabe: %s", exc)
        if self.sock:
            self.sock.close()
        self.sock = self.link = None
        self.locked = False


def save_pdf(output_dir: Path, pages, dpi: int) -> Path:
    """Schreibt ``scan_<Datum><Uhrzeit>.pdf``: erst als ``.part``, dann
    umbenennen, damit Paperless nie eine halbe Datei einliest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    target = output_dir / f"scan_{stamp}.pdf"
    n = 1
    while target.exists():  # kein Überschreiben
        n += 1
        target = output_dir / f"scan_{stamp}_{n}.pdf"
    part = target.with_name(target.name + ".part")
    write_pdf(part, [(pg.jpeg(), pg.width, pg.height) for pg in pages], dpi)
    part.rename(target)
    return target


def _token(data: bytes, name: bytes) -> str:
    """Wert eines ``#<name>h<len>``-Tokens, z. B. ``#PRDh008DS-560`` -> ``DS-560``."""
    start = data.find(b"#" + name)
    if start < 0:
        return "?"
    end = data.find(b"#", start + 1)
    raw = data[start + 1 + len(name) : end if end > 0 else None]
    if raw[:1] == b"h":
        raw = raw[4 : 4 + int(raw[1:4], 16)]
    return raw.decode(errors="replace").strip()
