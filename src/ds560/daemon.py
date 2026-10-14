"""Einstiegspunkt des Daemons: Dauerschleife mit Reconnect und Backoff.

Der Daemon läuft „always on": nach jedem Verbindungsabbruch wird mit wachsender
Wartezeit (backoff_start -> backoff_max) neu verbunden. Vor jedem Neuaufbau
wird die Scanner-IP neu ermittelt. Eine regulär abgelaufene Sitzung (0x9000/80)
wird ohne Wartezeit neu aufgebaut.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from .config import Config, load_config
from .discovery import find_scanner
from .session import Session, SessionEnded

log = logging.getLogger("ds560d")


def candidate_hosts(config: Config, fresh: bool) -> list[str]:
    """Feste IP aus der Konfiguration, sonst Suche über die MAC."""
    sc = config.scanner
    if sc.host:
        return [sc.host]
    if sc.mac and sc.subnet:
        return find_scanner(sc.mac, sc.subnet, fresh=fresh)
    raise SystemExit("config.toml: entweder scanner.host oder scanner.mac + scanner.subnet setzen")


def _terminate(signum, _frame):
    raise SystemExit(f"Signal {signum}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ds560d", description=__doc__)
    parser.add_argument("--config", default="config.toml", help="Pfad zur config.toml")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--trace", action="store_true", help="jeden IS-Rahmen loggen (DEBUG)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # systemd stoppt mit SIGTERM: als SystemExit durchreichen, damit die
    # Sitzung im finally sauber freigegeben wird.
    signal.signal(signal.SIGTERM, _terminate)

    config = load_config(args.config)
    backoff = config.session.backoff_start_seconds
    fresh = False

    log.info("DS-560-Daemon startet (Gerät %s)", config.scanner.device_id)
    while True:
        hosts = candidate_hosts(config, fresh)
        if not hosts:
            log.warning("Scanner nicht gefunden, neuer Versuch in %s s", backoff)
        for host in hosts:
            session = Session(config, host, trace=args.trace)
            try:
                session.connect()
                backoff = config.session.backoff_start_seconds  # Reset nach Erfolg
                fresh = False
                session.run_until_disconnect()
            except SessionEnded as exc:
                log.info("%s — verbinde neu", exc)
                backoff = 0
                break
            except OSError as exc:
                log.warning("Verbindung zu %s: %s", host, exc)
            except Exception:  # noqa: BLE001 — Daemon soll nie durchfallen
                log.exception("Sitzung mit %s abgebrochen", host)
            finally:
                session.close()
        else:
            # Kein Host lieferte eine Sitzung: beim nächsten Mal neu suchen.
            fresh = True

        if backoff:
            log.info("Reconnect in %s s", backoff)
            time.sleep(backoff)
        backoff = min(max(backoff * 2, config.session.backoff_start_seconds),
                      config.session.backoff_max_seconds)


if __name__ == "__main__":
    sys.exit(main())
