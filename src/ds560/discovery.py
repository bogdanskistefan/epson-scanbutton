"""Scanner-IP über die MAC-Adresse finden (wie Epson Scan mit ``Auto IP = 1``).

Der DS-560 bekommt seine IP per DHCP und antwortet weder auf mDNS noch auf
SSDP. Verlässlich ist der ARP-Cache: Nach je einem UDP-Paket an jede Adresse
des Subnetzes kennt der Kernel die MAC aller Geräte, die gerade online sind.
Dafür braucht es keine Root-Rechte. Im Docker-Container geht das nur mit
``--network host``.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

ARP_TABLE = Path("/proc/net/arp")
_DISCARD_PORT = 9  # UDP "discard": Inhalt egal, es geht nur um die ARP-Anfrage
_SWEEP_CHUNK = 32  # Hosts pro Socket, siehe sweep()


def lookup_arp(mac: str) -> list[str]:
    """Alle IPs zu ``mac`` mit vollständigem Eintrag im ARP-Cache.

    Nach einem DHCP-Wechsel kann neben der neuen auch noch die alte IP stehen;
    der Aufrufer probiert deshalb alle.
    """
    mac = mac.lower()
    try:
        lines = ARP_TABLE.read_text().splitlines()[1:]
    except OSError:
        return []
    ips = []
    for line in lines:
        fields = line.split()
        # IP  HW-Typ  Flags  HW-Adresse  Maske  Gerät; Flags 0x0 = unvollständig
        if len(fields) >= 4 and fields[3].lower() == mac and fields[2] != "0x0":
            ips.append(fields[0])
    return ips


def sweep(subnet: str) -> None:
    """Je ein UDP-Paket an alle Hosts des Subnetzes, damit der Kernel ARP auflöst.

    Pakete an noch unaufgelöste Adressen belegen den Sendepuffer, bis ARP
    antwortet oder aufgibt (~3 s); über einen einzigen Socket blockiert
    ``sendto`` nach gut 200 Paketen. Daher je ``_SWEEP_CHUNK`` Hosts ein Socket.
    """
    hosts = [str(h) for h in ipaddress.ip_network(subnet, strict=False).hosts()]
    for i in range(0, len(hosts), _SWEEP_CHUNK):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for host in hosts[i : i + _SWEEP_CHUNK]:
                try:
                    s.sendto(b"", (host, _DISCARD_PORT))
                except OSError:
                    pass


def find_scanner(
    mac: str, subnet: str, exclude: Iterable[str] = (), wait_seconds: float = 2.0
) -> list[str]:
    """Subnetz absuchen und IPs zu ``mac`` liefern, außer denen in ``exclude``.

    Kehrt zurück, sobald eine neue IP im ARP-Cache auftaucht, spätestens nach
    ``wait_seconds``. ``exclude`` sind bereits erfolglos probierte IPs — ihr
    ARP-Eintrag kann veraltet sein und darf die Suche nicht vorzeitig beenden.
    """
    exclude = set(exclude)
    log.info("Suche Scanner %s in %s", mac, subnet)
    sweep(subnet)
    deadline = time.monotonic() + wait_seconds
    while True:
        found = [ip for ip in lookup_arp(mac) if ip not in exclude]
        if found or time.monotonic() >= deadline:
            return found
        time.sleep(0.1)
