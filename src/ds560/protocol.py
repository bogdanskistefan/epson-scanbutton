"""Wire-Protokoll des Epson DS-560 auf TCP 1865.

Beschrieben in docs/protocol.md.

Zwei Schichten:

- **IS-Rahmen** (Netzschicht, wie SANE ``epson2``/``epsonds``): 12-Byte-Header

      Offset  Länge  Feld
      0       2      "IS"
      2       2      Pakettyp, z. B. 0x2000
      4       1      Richtung: PC->Scanner 0x00, Scanner->PC 0x30
      5       1      Header-Länge 0x0c
      6       4      Länge der folgenden Nutzdaten
      10      2      00 00

  Bei Typ ``0x2000`` beginnen die Nutzdaten mit 8 Byte: Länge der Sendedaten (4)
  und erwartete Antwortlänge (4), danach die eigentlichen Daten.

- **ESC/I-2** (Scanner-Befehle) in den Nutzdaten: 12-Byte-Befehlskopf
  ``<CMD>x<Länge 7-stellig hex>`` (z. B. ``INFOx0000000``) als eigener 0x2000-Rahmen.
  Ablauf (aus startup.pcapng, deckt sich mit SANE ``epsonds``):

  1. ohne Parameter: Kopf mit Antwortlänge 64.
     Mit Parametern (``RESAx0000004`` + ``#ADF``, ``PARA``): Kopf mit Antwortlänge 0
     (Scanner bestätigt mit leerem 0xa000), dann die Parameter mit Antwortlänge 64.
  2. Antwort: 64-Byte-Kopf ``<CMD>x<Länge hex>#nrdNONE#…#---``, Rest Nullbytes.
  3. Länge > 0: leerer 0x2000-Rahmen mit Antwortlänge = Länge holt den Datenblock.

Alle Mehrbyte-Zahlen sind Big-Endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --- IS-Rahmen ------------------------------------------------------------
IS_MAGIC = b"IS"
IS_HEADER_LEN = 12
DIR_TO_SCANNER = 0x00
DIR_FROM_SCANNER = 0x30

# Pakettypen (16 bit)
# Der Scanner antwortet auf einen PC-Typ mit demselben Typ | 0x8000.
TYPE_DATA = 0x2000        # PC -> Scanner: Daten/ESC/I-2-Befehl (8 Byte Längen + Daten)
TYPE_DATA_ACK = 0xA000    # Scanner -> PC: genau die angeforderte Antwortlänge (auch 0)
TYPE_LOCK = 0x2100        # PC -> Scanner: Sitzung belegen
TYPE_LOCK_ACK = 0xA100    # Scanner -> PC: 06
TYPE_UNLOCK = 0x2101      # PC -> Scanner: leer, Sitzung freigeben
TYPE_UNLOCK_ACK = 0xA101  # Scanner -> PC: leer
TYPE_KEEPALIVE = 0x1000   # PC -> Scanner: leer; Scanner bestätigt nur per TCP-ACK
TYPE_GREETING = 0x8000    # Scanner -> PC: direkt nach dem Connect, Nutzdaten 01 02 00 00 00
TYPE_NOTIFY = 0x9000      # Scanner -> PC: 1 Byte Benachrichtigung

# Nutzdaten der Lock-Anforderung, identisch mit SANE epson2. Bedeutung von
# 0x012c (300) ist offen — laut Timeout-Analyse nicht das Leerlauf-Timeout.
LOCK_PAYLOAD = bytes.fromhex("01a0040000012c")

# Werte des 0x9000-Bytes
NOTIFY_BUTTON = 0x01      # Scan-Knopf gedrückt
NOTIFY_SESSION_END = 0x80  # Sitzung läuft ab (Leerlauf-Timeout), danach TCP FIN

ACK = 0x06

# --- ESC/I-2 --------------------------------------------------------------
FS_X = b"\x1cX"   # in den normalen Scanmodus wechseln
FS_Z = b"\x1cZ"   # Wartungsmodus (Zählerstände)

CMD_INFO = b"INFO"
CMD_CAPA = b"CAPA"
CMD_CAPB = b"CAPB"
CMD_RESA = b"RESA"
CMD_STAT = b"STAT"
CMD_PARA = b"PARA"
CMD_TRDT = b"TRDT"
CMD_IMG = b"IMG "
CMD_FIN = b"FIN "

ESCI2_CMD_HEADER_LEN = 12
ESCI2_REPLY_HEADER_LEN = 64

# Im IMG-/STAT-Kopf: Einzug leer, Scan beendet.
ERR_ADF_EMPTY = b"#ERRADF PE"


@dataclass(frozen=True)
class Frame:
    """Ein IS-Rahmen."""

    type: int
    direction: int
    payload: bytes


def encode_frame(ptype: int, payload: bytes = b"") -> bytes:
    """Baut einen IS-Rahmen PC -> Scanner."""
    header = IS_MAGIC + struct.pack(
        ">HBBIH", ptype, DIR_TO_SCANNER, IS_HEADER_LEN, len(payload), 0
    )
    return header + payload


def encode_data(data: bytes, reply_len: int) -> bytes:
    """Baut einen 0x2000-Rahmen: Sendelänge, erwartete Antwortlänge, Daten."""
    return encode_frame(TYPE_DATA, struct.pack(">II", len(data), reply_len) + data)


def parse_header(header: bytes) -> tuple[int, int, int]:
    """Zerlegt einen 12-Byte-IS-Header in (Typ, Richtung, Nutzdatenlänge)."""
    if len(header) != IS_HEADER_LEN or header[:2] != IS_MAGIC:
        raise ValueError(f"kein IS-Header: {header!r}")
    ptype, direction, hlen, length, _ = struct.unpack(">HBBIH", header[2:])
    if hlen != IS_HEADER_LEN:
        raise ValueError(f"unerwartete Header-Länge {hlen:#x}")
    return ptype, direction, length


def esci2_header(cmd: bytes, params_len: int = 0) -> bytes:
    """Baut den 12-Byte-ESC/I-2-Befehlskopf ``<CMD>x<Länge der Parameter>``."""
    if len(cmd) != 4:
        raise ValueError(f"ESC/I-2-Befehl muss 4 Byte lang sein: {cmd!r}")
    return cmd + b"x%07X" % params_len


@dataclass(frozen=True)
class Reply:
    """Kopf einer ESC/I-2-Antwort."""

    cmd: bytes
    data_len: int
    tokens: bytes  # z. B. b"#nrdNONE#parOK  " — ohne abschließendes "#---"


def parse_reply_header(header: bytes) -> Reply:
    """Zerlegt den 64-Byte-Antwortkopf ``<CMD>x<Länge hex>#…#---``."""
    if len(header) < ESCI2_CMD_HEADER_LEN or header[4:5] != b"x":
        raise ValueError(f"kein ESC/I-2-Antwortkopf: {header[:16]!r}")
    data_len = int(header[5:12], 16)
    rest = header[12:].rstrip(b"\x00")
    end = rest.find(b"#---")
    return Reply(header[:4], data_len, rest if end < 0 else rest[:end])
