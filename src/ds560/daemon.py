"""Einstiegspunkt des Daemons: Dauerschleife mit Reconnect.

Der Daemon läuft „always on". Zwei Fälle werden unterschieden:

- **Scanner nicht erreichbar** (aus, Energiesparen, noch nicht im WLAN): der
  Normalfall, kein Fehler. Es wird alle ``offline_poll_seconds`` die zuletzt
  bekannte IP angefragt, damit die Sitzung steht, bevor jemand nach dem
  Einschalten den Knopf drückt — einen Knopfdruck ohne Sitzung vergisst der
  Scanner. Alle ``sweep_interval_seconds`` wird zusätzlich das Subnetz nach
  der MAC abgesucht, falls DHCP eine neue IP vergeben hat.
- **Fehler in einer bestehenden Verbindung** (Protokoll, Lock abgelehnt …):
  Neuaufbau mit wachsender Wartezeit (backoff_start -> backoff_max).

Eine regulär abgelaufene Sitzung (0x9000/80) wird sofort neu aufgebaut.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from .config import Config, load_config
from .discovery import find_scanner, lookup_arp
from .session import Session, SessionEnded

log = logging.getLogger("ds560d")


class Finder:
    """Liefert Kandidaten-IPs; die zuletzt erfolgreiche IP immer zuerst.

    Ist der Scanner aus, fällt sein ARP-Eintrag weg — die gemerkte IP bleibt,
    so dass er nach dem Einschalten ohne Sweep sofort gefunden wird. Gesweept
    wird nur, wenn keine bekannte IP antwortet, und höchstens alle
    ``sweep_interval_seconds``.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.last_ok: str | None = config.scanner.host
        self.last_sweep = float("-inf")
        sc = config.scanner
        if not (sc.host or (sc.mac and sc.subnet)):
            raise SystemExit(
                "config.toml: entweder scanner.host oder scanner.mac + scanner.subnet setzen")

    def candidates(self) -> list[str]:
        """Schnelle Kandidaten ohne Netzlast: gemerkte IP + ARP-Cache."""
        sc = self.config.scanner
        if sc.host:
            return [sc.host]
        hosts = [self.last_ok] if self.last_ok else []
        return hosts + [ip for ip in lookup_arp(sc.mac) if ip not in hosts]

    def sweep(self, tried: list[str]) -> list[str]:
        """Neue Kandidaten per Subnetz-Sweep, falls fällig; sonst leer."""
        sc = self.config.scanner
        interval = self.config.session.sweep_interval_seconds
        if sc.host or time.monotonic() - self.last_sweep < interval:
            return []
        self.last_sweep = time.monotonic()
        return find_scanner(sc.mac, sc.subnet, exclude=tried)

    def connected(self, host: str) -> None:
        self.last_ok = host


def _terminate(signum, _frame):
    raise SystemExit(f"Signal {signum}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ds560d", description=__doc__)
    parser.add_argument("--config", default="config.toml", help="Pfad zur config.toml")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--trace", action="store_true", help="jeden IS-Rahmen loggen (DEBUG)")
    parser.add_argument("--buttons-only", action="store_true",
                        help="Knopfdrücke nur protokollieren, nicht scannen (Diagnose)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # systemd stoppt mit SIGTERM: als SystemExit durchreichen, damit die
    # Sitzung im finally sauber freigegeben wird.
    signal.signal(signal.SIGTERM, _terminate)

    config = load_config(args.config)
    se = config.session
    finder = Finder(config)
    backoff = se.backoff_start_seconds
    offline_since: float | None = None

    log.info("DS-560-Daemon startet (Gerät %s)", config.scanner.device_id)
    while True:
        delay: float | None = None  # None: kein Kandidat erreichbar
        tried: list[str] = []
        hosts = finder.candidates() or finder.sweep([])
        while delay is None and hosts:
            host = hosts.pop(0)
            tried.append(host)
            session = Session(config, host, trace=args.trace, scan=not args.buttons_only)
            try:
                try:
                    session.connect()
                except OSError as exc:
                    log.debug("%s nicht erreichbar: %s", host, exc)
                    if not hosts:
                        hosts = finder.sweep(tried)
                    continue
                finder.connected(host)
                if offline_since is not None:
                    log.info("Scanner wieder erreichbar (nach %.0f s)",
                             time.monotonic() - offline_since)
                offline_since = None
                backoff = se.backoff_start_seconds
                session.run_until_disconnect()
            except SessionEnded as exc:
                log.info("%s — verbinde neu", exc)
                delay = 0
            except OSError as exc:
                # Bestehende Verbindung weg, meist ausgeschaltet: sofort neu prüfen,
                # damit die Sitzung nach dem Einschalten schnell wieder steht.
                log.warning("Verbindung zu %s verloren: %s", host, exc)
                delay = 0
            except Exception:  # noqa: BLE001 — Daemon soll nie durchfallen
                log.exception("Sitzung mit %s abgebrochen, neuer Versuch in %s s", host, backoff)
                delay = backoff
                backoff = min(backoff * 2, se.backoff_max_seconds)
            finally:
                session.close()

        if delay is None:
            if offline_since is None:
                offline_since = time.monotonic()
                log.warning("Scanner nicht erreichbar — prüfe alle %s s", se.offline_poll_seconds)
            delay = se.offline_poll_seconds
        if delay:
            time.sleep(delay)

if __name__ == "__main__":
    sys.exit(main())
