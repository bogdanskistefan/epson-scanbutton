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
from pathlib import Path

log = logging.getLogger(__name__)

ARP_TABLE = Path("/proc/net/arp")
_DISCARD_PORT = 9  # UDP "discard": Inhalt egal, es geht nur um die ARP-Anfrage


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
    """Je ein UDP-Paket an alle Hosts des Subnetzes, damit der Kernel ARP auflöst."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for host in ipaddress.ip_network(subnet, strict=False).hosts():
            try:
                s.sendto(b"", (str(host), _DISCARD_PORT))
            except OSError:
                pass


def find_scanner(
    mac: str, subnet: str, fresh: bool = False, wait_seconds: float = 3.0
) -> list[str]:
    """Kandidaten-IPs zu ``mac``: aus dem ARP-Cache, sonst nach einem Sweep.

    ``fresh`` erzwingt den Sweep, z. B. wenn keine IP aus dem Cache erreichbar
    war. Dann wird die volle Wartezeit abgewartet, damit neue Einträge auftauchen.
    """
    if not fresh:
        ips = lookup_arp(mac)
        if ips:
            return ips
    log.info("Suche Scanner %s in %s", mac, subnet)
    sweep(subnet)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if not fresh and lookup_arp(mac):
            break
    return lookup_arp(mac)
