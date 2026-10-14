# epson-scanbutton

Use the **scan button of an Epson DS-560 over Wi-Fi on Linux** — no Windows,
no Epson Scan. A small daemon keeps a session with the scanner open, waits for
the button, scans duplex and writes one PDF per stack into a folder, for
example the consume folder of [Paperless-ngx](https://docs.paperless-ngx.com/).

SANE can scan from this scanner, but nothing on Linux reacts to its button over
the network: the scanner delivers the button press as a push message on its
network session. This project implements that protocol.

## Features

- Button press → duplex scan → one PDF (JPEGs from the scanner embedded
  losslessly, no re-encoding)
- Always on: keepalive, automatic reconnect with backoff, clean shutdown on
  SIGTERM
- Finds the scanner by its MAC address when its DHCP address changes
- Scan settings (resolution, colour, duplex, JPEG quality, double-feed
  detection) in a plain TOML file
- Pure Python ≥ 3.11, no dependencies

What the scanner cannot do itself — OCR, blank page removal, deskew — is left
to the document system downstream (Paperless-ngx does OCR and deskew).

## Status

Tested with an **Epson DS-560** (firmware `IPL 1.00 / ADF 1.05`). Other Epson
network document scanners with ESC/I-2 (e.g. DS-570W, DS-770, DS-780N) may
speak the same protocol — not tested. Reports are welcome.

Not yet done: blank page removal, retry/spool when the target folder is
unreachable, packaging.

## Setup

```bash
git clone https://github.com/bogdanskistefan/epson-scanbutton.git
cd epson-scanbutton
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.toml config.toml   # set MAC + subnet (or a fixed host) and output_dir
.venv/bin/ds560d --config config.toml
```

Close Epson Scan on all other computers: the scanner reports the button only to
the client that holds its session.

Load paper **face down, top edge first**.

Check connectivity without scanning:

```bash
.venv/bin/python tools/probe.py <scanner-ip>
```

### Running as a service

`systemd/ds560d.service` is a template; fill in the placeholders and install
it as a system or user unit. In a container, scanner discovery by MAC needs
`--network host`; with a fixed `host` in the config, bridge networking works.

## Configuration

See [`config.example.toml`](config.example.toml). The `[scan]` values are
checked against what the scanner supports (`CAPA`).

## How it works

```
ds560d ──TCP 1865──▶ DS-560
  lock session, keepalive every 60 s
  ◀── 0x9000/01 (button)
  PARA → TRDT → IMG … → JPEG pages → PDF → output_dir
```

The protocol is documented in [`docs/protocol.md`](docs/protocol.md).

| Module | Purpose |
|---|---|
| `daemon.py` | main loop, reconnect, backoff |
| `discovery.py` | find the scanner IP via ARP |
| `session.py` | one connection: handshake, keepalive, button |
| `link.py` | frame I/O, ESC/I-2 transactions |
| `protocol.py` | wire format constants and encoding |
| `scan.py` | scan sequence, JPEG fix-ups |
| `pdf.py` | minimal PDF writer |
| `config.py` | config loading and validation |

## Development

```bash
.venv/bin/pip install pytest pillow ruff
.venv/bin/pytest
.venv/bin/ruff check src tests tools
```

The tests replay byte sequences and scan flows taken from real captures.

## Disclaimer

Not affiliated with or endorsed by Seiko Epson. "Epson" is a trademark of Seiko
Epson Corporation and is used here only to name the hardware this works with.
The protocol was documented by observing the network traffic between the
scanner and its vendor software, for interoperability. No Epson software,
firmware or code is included.

## License

[Apache License 2.0](LICENSE)
