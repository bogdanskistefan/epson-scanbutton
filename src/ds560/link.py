"""Rahmen-Ein-/Ausgabe auf einem offenen Socket zum DS-560.

``Link`` kennt IS-Rahmen und den ESC/I-2-Transaktionsablauf, aber keinen
Sitzungszustand. Benachrichtigungen (``0x9000``) können jederzeit eintreffen,
auch mitten in einer Transaktion; sie werden dann zwischengespeichert und über
``pop_notifications`` abgeholt.
"""

from __future__ import annotations

import logging
import re
import socket
from collections import deque

from . import protocol as p

log = logging.getLogger(__name__)


class ProtocolError(RuntimeError):
    """Der Scanner hat etwas Unerwartetes geschickt."""


def mask_serial(data: bytes) -> bytes:
    """Maskiert die Seriennummer (``#S/N``) für Logs."""
    return re.sub(rb"(#S/Nh[0-9A-F]{3})[^#]*", rb"\1XXXX", data)


class Link:
    def __init__(self, sock: socket.socket, trace: bool = False) -> None:
        self.sock = sock
        self.trace = trace
        self._notifications: deque[int] = deque()

    # --- Rahmen ----------------------------------------------------------
    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Scanner hat die Verbindung geschlossen")
            buf += chunk
        return bytes(buf)

    def _show(self, direction: str, ptype: int, payload: bytes) -> None:
        if self.trace:
            log.debug("%s %#06x len=%-5d %s", direction, ptype, len(payload),
                      mask_serial(payload)[:96].hex(" "))

    def recv_frame(self) -> p.Frame:
        """Liest genau einen Rahmen, auch eine Benachrichtigung."""
        ptype, direction, length = p.parse_header(self._recv_exact(p.IS_HEADER_LEN))
        payload = self._recv_exact(length)
        self._show("S>PC", ptype, payload)
        return p.Frame(ptype, direction, payload)

    def send_frame(self, frame: bytes) -> None:
        ptype, _, _ = p.parse_header(frame[: p.IS_HEADER_LEN])
        self._show("PC>S", ptype, frame[p.IS_HEADER_LEN :])
        self.sock.sendall(frame)

    def expect(self, ptype: int) -> p.Frame:
        """Liest bis zum nächsten Rahmen vom Typ ``ptype``; Benachrichtigungen
        dazwischen werden zwischengespeichert."""
        while True:
            frame = self.recv_frame()
            if frame.type == p.TYPE_NOTIFY:
                self._notifications.append(frame.payload[0] if frame.payload else -1)
                continue
            if frame.type != ptype:
                raise ProtocolError(f"erwartet {ptype:#06x}, bekommen {frame.type:#06x}")
            return frame

    def pop_notifications(self) -> list[int]:
        items = list(self._notifications)
        self._notifications.clear()
        return items

    # --- Transaktionen ---------------------------------------------------
    def transact(self, data: bytes, reply_len: int) -> bytes:
        """Daten per 0x2000 senden, Antwort (0xa000) mit ``reply_len`` Byte lesen."""
        self.send_frame(p.encode_data(data, reply_len))
        payload = self.expect(p.TYPE_DATA_ACK).payload
        if len(payload) != reply_len:
            raise ProtocolError(f"Antwortlänge {len(payload)} statt {reply_len}")
        return payload

    def esci2(self, cmd: bytes, params: bytes = b"") -> tuple[p.Reply, bytes]:
        """Ein ESC/I-2-Befehl: Kopf (+Parameter) senden, Antwortkopf lesen,
        ggf. den Datenblock abholen."""
        if params:
            self.transact(p.esci2_header(cmd, len(params)), 0)
            header = self.transact(params, p.ESCI2_REPLY_HEADER_LEN)
        else:
            header = self.transact(p.esci2_header(cmd), p.ESCI2_REPLY_HEADER_LEN)
        reply = p.parse_reply_header(header)
        if reply.cmd != cmd:
            raise ProtocolError(f"Antwort auf {cmd!r} ist {reply.cmd!r}")
        data = self.transact(b"", reply.data_len) if reply.data_len else b""
        return reply, data

    def mode(self, fs: bytes) -> None:
        """In den ESC/I-2-Modus ``FS X`` oder ``FS Z`` wechseln."""
        if self.transact(fs, 1) != bytes([p.ACK]):
            raise ProtocolError(f"kein ACK auf {fs!r}")

    # --- Sitzung belegen / freigeben -------------------------------------
    def open(self) -> None:
        """Begrüßung lesen und die Sitzung locken."""
        greeting = self.recv_frame()
        if greeting.type != p.TYPE_GREETING:
            raise ProtocolError(f"keine Begrüßung, sondern {greeting.type:#06x}")
        self.send_frame(p.encode_frame(p.TYPE_LOCK, p.LOCK_PAYLOAD))
        if self.expect(p.TYPE_LOCK_ACK).payload != bytes([p.ACK]):
            raise ProtocolError("Lock abgelehnt — belegt ein anderes Programm den Scanner?")

    def unlock(self) -> None:
        self.send_frame(p.encode_frame(p.TYPE_UNLOCK))
        self.expect(p.TYPE_UNLOCK_ACK)

    def keepalive(self) -> None:
        """Keepalive senden; der Scanner bestätigt nur per TCP-ACK."""
        self.send_frame(p.encode_frame(p.TYPE_KEEPALIVE))
