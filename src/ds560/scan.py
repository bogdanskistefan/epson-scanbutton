"""Scan-Lauf: Einstellungen -> PARA, Bilddaten per IMG abholen, Seiten zusammensetzen.

Ablauf (siehe docs/protocol.md, „Scanning"):

    PARA #ADFDPLX -> STAT -> PARA <alle Einstellungen> -> TRDT
    IMG … (leer, solange der Einzug anläuft)
        #pst<w><h>#typIMGA   Seitenanfang Vorderseite (h = angefragte Höhe)
        <Länge>#typIMGA      Datenblock für die Vorderseite
        #pen<w><h>#typIMGA   Seitenende, h = tatsächliche Höhe
        (IMGB analog für die Rückseite, Abfragen wechseln sich ab)
    IMG #errADF PE           Einzug leer -> fertig
    STAT

Die Firmware liefert pro Seite ein JPEG, das nicht mit EOI endet, sondern mit
Füllbytes bis zu einem Vielfachen von 64; außerdem steht im SOF noch die
angefragte statt der tatsächlichen Höhe. ``finish_jpeg`` korrigiert beides.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import protocol as p
from .link import Link

log = logging.getLogger(__name__)

IMG_POLL_INTERVAL = 0.05  # Pause nach einer leeren IMG-Antwort
IMG_EMPTY_TIMEOUT = 60.0  # so lange darf der Scanner ohne Daten bleiben


class ScanError(RuntimeError):
    """Der Scanner hat den Scan mit einem Fehler beendet (Stau, Doppeleinzug …)."""


@dataclass
class ScanSettings:
    """Scan-Optionen für ``PARA``; befüllt aus der [scan]-Sektion der config.toml.
    Standardwerte = Mitschnitt vom 13.10."""

    duplex: bool = True
    double_feed: str = "DFL1"   # "" = aus, DFL1/DFL2 = Empfindlichkeit
    color: str = "C024"         # C024 Farbe, M008 Grau, M001 S/W
    threshold: int = 110        # Schwelle nur für M001 (#THRRANGd000d255)
    overscan: bool = True       # OVSN: Epson Scan sendet es immer mit
    jpeg_quality: int = 85
    gamma: str = "UG18"
    resolution: int = 600       # 300 oder 600
    width_inch: float = 8.5     # Einzugsbreite (#ADFAREA 850)
    height_inch: float = 14.0   # maximale Seitenlänge; die Firmware kürzt selbst

    def para(self) -> bytes:
        adf = (b"#ADF" + (b"DPLX" if self.duplex else b"") + self.double_feed.encode()
               + (b"OVSN" if self.overscan else b""))
        w = round(self.width_inch * self.resolution)
        h = round(self.height_inch * self.resolution)
        return b"".join([
            adf,
            b"#COL" + self.color.encode(),
            # Format von #THR nach CAPA (#THRRANGd000d255) abgeleitet, ungetestet.
            b"#THRd%03d" % self.threshold if self.color == "M001" else b"",
            b"#FMTJPG ",
            b"#JPGd%03d" % self.jpeg_quality,
            b"#GMM" + self.gamma.encode(),
            b"#CMXUNIT",
            b"#RSMi%07d" % self.resolution,
            b"#RSSi%07d" % self.resolution,
            b"#ACQi%07di%07di%07di%07d" % (0, 0, w, h),
            b"#BSZi%07d" % 1048576,
            b"#PAGd000",
        ])


def parse_tokens(tokens: bytes) -> dict[str, bytes]:
    """``#pst…#typIMGA`` -> {"pst": b"…", "typ": b"IMGA"} (Name = 3 Zeichen)."""
    out = {}
    for part in tokens.split(b"#"):
        part = part.strip(b"\x00")
        if len(part) >= 3:
            out[part[:3].decode("latin-1")] = part[3:]
    return out


def parse_dims(value: bytes) -> tuple[int, int]:
    """Breite und Höhe: ``#pen i0005100i0007537`` -> (5100, 7537);
    ``#pst i0005100i0000000i0008872`` (Breite, ?, Höhe) -> (5100, 8872)."""
    nums = [int(x) for x in value.split(b"i") if x]
    return nums[0], nums[-1]


def finish_jpeg(data: bytes, height: int | None) -> bytes:
    """Schneidet nach dem letzten EOI ab und trägt die echte Höhe ins SOF ein."""
    eoi = data.rfind(b"\xff\xd9")
    if eoi >= 0:
        data = data[: eoi + 2]
    else:
        log.warning("JPEG ohne EOI — hänge eins an")
        data = data + b"\xff\xd9"
    if height:
        data = bytearray(data)
        i = 2
        while i + 4 < len(data) and data[i] == 0xFF:
            marker, seglen = data[i + 1], int.from_bytes(data[i + 2 : i + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2):  # SOF: FF Cx len P H(2) W(2)
                data[i + 5 : i + 7] = height.to_bytes(2, "big")
                break
            i += 2 + seglen
        data = bytes(data)
    return data


@dataclass
class Page:
    side: str            # "IMGA" (Vorderseite) / "IMGB" (Rückseite)
    width: int = 0
    height: int = 0
    chunks: list[bytes] = field(default_factory=list)

    def jpeg(self) -> bytes:
        return finish_jpeg(b"".join(self.chunks), self.height)


def run_scan(link: Link, settings: ScanSettings) -> list[Page]:
    """Führt einen Scan aus und gibt die Seiten in Lesereihenfolge zurück
    (Blatt 1 vorn, Blatt 1 hinten, Blatt 2 vorn …)."""
    reply, _ = link.esci2(p.CMD_PARA, b"#ADFDPLX" if settings.duplex else b"#ADF")
    _check_par(reply)
    link.esci2(p.CMD_STAT)
    reply, _ = link.esci2(p.CMD_PARA, settings.para())
    _check_par(reply)
    t0 = time.monotonic()
    link.esci2(p.CMD_TRDT)
    log.info("TRDT bestätigt nach %.1f s", time.monotonic() - t0)
    first_data: float | None = None

    open_pages: dict[str, Page] = {}
    done: dict[str, list[Page]] = {"IMGA": [], "IMGB": []}
    last_data = time.monotonic()
    while True:
        reply, data = link.esci2(p.CMD_IMG)
        tok = parse_tokens(reply.tokens)
        if "err" in tok:
            err = tok["err"].decode("latin-1").strip()
            if err == "ADF PE":
                break
            raise ScanError(f"Scanner meldet Fehler: {err}")
        side = tok.get("typ", b"").decode("latin-1")
        if "pst" in tok:
            w, h = parse_dims(tok["pst"])
            open_pages[side] = Page(side, w, h)
            log.debug("Seitenanfang %s %dx%d", side, w, h)
        if data:
            page = open_pages.get(side)
            if page is None:
                raise ScanError(f"Daten für {side!r} ohne Seitenanfang")
            page.chunks.append(data)
            last_data = time.monotonic()
            if first_data is None:
                first_data = last_data
                log.info("Erste Bilddaten %.1f s nach TRDT", first_data - t0)
        if "pen" in tok:
            page = open_pages.pop(side)
            page.width, page.height = parse_dims(tok["pen"])
            done[side].append(page)
            log.info("Seite %s %d fertig: %dx%d, %d KB", side, len(done[side]),
                     page.width, page.height, sum(map(len, page.chunks)) // 1024)
        if not data and "pen" not in tok and "pst" not in tok:
            if time.monotonic() - last_data > IMG_EMPTY_TIMEOUT:
                raise ScanError("Scanner liefert keine Daten mehr")
            time.sleep(IMG_POLL_INTERVAL)

    _, stat = link.esci2(p.CMD_STAT)
    log.debug("STAT nach Scan: %s", stat)
    if open_pages:
        log.warning("Unvollständige Seiten verworfen: %s", list(open_pages))
    pages = []
    for i, front in enumerate(done["IMGA"]):
        pages.append(front)
        if i < len(done["IMGB"]):
            pages.append(done["IMGB"][i])
    return pages


def _check_par(reply: p.Reply) -> None:
    if b"parOK" not in reply.tokens:
        raise ScanError(f"PARA abgelehnt: {reply.tokens!r}")
