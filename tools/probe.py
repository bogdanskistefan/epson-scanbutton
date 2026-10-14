"""Fragt den DS-560 einmal ab: verbinden, locken, INFO/CAPA/RESA/STAT, freigeben.

Nur lesend — es wird nichts gescannt. Epson Scan darf währenddessen nicht offen
sein, weil der Scanner nur eine gelockte Sitzung bedient.

    python tools/probe.py <scanner-ip> [--raw] [--capb]

Die Seriennummer aus INFO (#S/N) wird in der Ausgabe maskiert.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ds560 import protocol as p  # noqa: E402
from ds560.link import Link, mask_serial  # noqa: E402


def print_tokens(title: str, reply: p.Reply, data: bytes) -> None:
    print(f"\n== {title}: {reply.cmd.decode()} Kopf {reply.tokens.decode(errors='replace')!r}")
    for token in mask_serial(data).split(b"#"):
        if token.strip(b"\x00"):
            print(f"   #{token.decode('latin-1')}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=1865)
    ap.add_argument("--raw", action="store_true", help="jeden IS-Rahmen als Hex ausgeben")
    ap.add_argument("--capb", action="store_true", help="zusätzlich CAPB senden (Rückseite)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.raw else logging.INFO, format="  %(message)s")

    with socket.create_connection((args.host, args.port), timeout=10) as sock:
        link = Link(sock, trace=args.raw)
        link.open()
        print("Sitzung gelockt")
        try:
            link.mode(p.FS_Z)
            for cmd in (p.CMD_INFO, p.CMD_CAPA):
                print_tokens("FS Z", *link.esci2(cmd))
            print_tokens("FS Z", *link.esci2(p.CMD_RESA, b"#ADF"))
            link.esci2(p.CMD_FIN)

            link.mode(p.FS_X)
            cmds = [p.CMD_INFO, p.CMD_CAPA, p.CMD_STAT] + ([p.CMD_CAPB] if args.capb else [])
            for cmd in cmds:
                print_tokens("FS X", *link.esci2(cmd))
            link.esci2(p.CMD_FIN)
        finally:
            link.unlock()
            print("\nSitzung freigegeben")
    return 0


if __name__ == "__main__":
    sys.exit(main())
