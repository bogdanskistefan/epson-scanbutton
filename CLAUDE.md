# CLAUDE.md

Hinweise für die Arbeit in diesem Repository.

## Zweck
Linux-Daemon (Python), der den **Scan-Button des Epson DS-560 über WLAN**
(TCP 1865) abfängt, einen Duplex-Scan auslöst und ein PDF ablegt — Ersatz für
„Epson Scan" unter Windows. Läuft dauerhaft (z. B. als `systemd`-Dienst) mit
Reconnect/Backoff. Öffentliches Repo (Apache-2.0), Doku für Nutzer auf Englisch.

## Protokoll-Quelle
`docs/protocol.md` ist die einzige Protokoll-Quelle. Konstanten und Abläufe
immer dagegen prüfen, nicht aus dem Gedächtnis raten; neue Erkenntnisse dort
nachtragen (nur aus Mitschnitten/Live-Tests, keine Disassembly-Details).

## Struktur
- `src/ds560/` — Python-Paket, ohne externe Abhängigkeiten:
  `daemon.py` (Dauerschleife/Reconnect), `discovery.py` (IP über MAC/ARP),
  `session.py` (Sitzungs-Zustandsautomat), `link.py` (Rahmen-E/A, ESC/I-2-Transaktionen),
  `protocol.py` (Wire-Format), `scan.py` (PARA/TRDT/IMG, JPEG-Korrektur),
  `pdf.py` (JPEGs → PDF), `config.py` (config.toml).
- `tools/probe.py` — einmalige, lesende Abfrage von INFO/CAPA/RESA/STAT.
- `tests/` — pytest; Bytes und Abläufe aus den Mitschnitten nachgestellt (`.venv/bin/pytest`).
- `systemd/ds560d.service` — Unit-Template (`<PLATZHALTER>` vor Install setzen).
- `captures/` — pcapng-Mitschnitte, gitignored.
- `config.toml` — lokal, gitignored (Geräte-/Netzdaten). Alle Scan-Einstellungen
  stehen dort; Epson-Dateien (`0000.ssf`, Registry) werden bewusst nicht gelesen.

## Konventionen
- Big-Endian im Wire-Protokoll; 4-Byte-ASCII-Befehle (`INFO`, `PARA`, `IMG `, …).
- Scanner-IP wechselt per DHCP → vor jedem Reconnect neu ermitteln.
- Keine Geräte-/Netzdaten (IPs, IDs) in Commits außer als Beispiel; die echte
  `config.toml` bleibt gitignored.
- Betriebsdoku (Aufsetzen, Deploy, Restore) gehört ins BookStack-Wiki, nicht in
  den Repo-Fließtext; code-nahe Doku (dieses CLAUDE.md, Architektur) bleibt hier.
