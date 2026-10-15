# Epson DS-560 network protocol (TCP 1865)

This describes how the Epson DS-560 talks to a host over Wi-Fi, as far as it
is needed to receive the scan button and run a scan. It was reverse-engineered
from packet captures of Epson Scan (Windows) talking to the scanner. Everything
here was verified against a real device with `tools/probe.py` and the daemon.
Statements that are not verified are marked as such.

The framing is the same network layer that SANE's `epson2`/`epsonds` backends
use; the scanner commands are ESC/I-2 as in SANE `epsonds`. What SANE does not
cover is the push notification for the scan button.

## Key facts

- The scanner is the TCP **server** on port **1865**. The host connects to it.
- There is no broadcast or callback for the button. The scanner sends a
  notification (`0x9000`) **on the open connection of whoever holds the
  session lock**. No lock, no button.
- Only one locked session at a time is expected. While the daemon runs, Epson
  Scan or SANE cannot use the scanner (lock behaviour of a second client not
  tested in detail).
- The scanner drops an idle session after about 15 minutes. A keepalive
  (`0x1000`) every 60 s keeps it open.
- When the scanner is switched off (by hand or by its power-off timer) it does
  **not** close the connection. The host only notices because its keepalives
  stay unacknowledged on TCP level; after power-on the scanner answers the old
  connection with a TCP reset. A button press while no session is open is not
  queued: it is lost and the scanner shows its error light.
- After a button notification, further presses are not reported for about
  3 s (observed: two or three quick presses give one `0x9000`, presses one
  second apart give one notification every ~3 s). Counting presses is
  therefore not practical.
- Other interfaces of the DS-560 (firmware `IPL 1.00 / ADF 1.05`): only TCP 80
  (Web Config) is open besides 1865. No eSCL, IPP, WSD, mDNS or SSDP.

## Framing ("IS" frames)

Every packet in both directions starts with a 12-byte header. All numbers are
big-endian.

```
Offset  Size  Field
0       2     "IS" (49 53)
2       2     packet type
4       1     direction: host->scanner 0x00, scanner->host 0x30
5       1     header length 0x0c
6       4     payload length
10      2     00 00
```

The scanner answers a host packet of type `T` with type `T | 0x8000`.

| Type | Direction | Payload | Meaning |
|---|---|---|---|
| `0x8000` | scanner → host | `01 02 00 00 00` | greeting, sent right after TCP connect |
| `0x2100` | host → scanner | `01 a0 04 00 00 01 2c` | lock the session (meaning of `0x012c` unknown) |
| `0xa100` | scanner → host | `06` | lock acknowledged |
| `0x2101` | host → scanner | empty | unlock |
| `0xa101` | scanner → host | empty | unlock acknowledged |
| `0x2000` | host → scanner | 4 B data length, 4 B expected reply length, data | send data / command |
| `0xa000` | scanner → host | exactly the requested reply length (may be 0) | reply to `0x2000` |
| `0x1000` | host → scanner | empty | keepalive; acknowledged only on TCP level |
| `0x9000` | scanner → host | 1 byte | notification: `01` = scan button pressed, `80` = session expires (followed by TCP FIN) |

The host does not acknowledge `0x9000` on the protocol level. Notifications can
arrive between any two transactions.

## ESC/I-2 commands

Mode switches are sent as raw data in a `0x2000` frame with reply length 1; the
scanner answers `06`:

- `FS X` (`1c 58`): normal scanning mode
- `FS Z` (`1c 5a`): maintenance mode (counters, timers)

All other commands use a 12-byte header `<CMD>x<7 hex digits>`, e.g.
`INFOx0000000`. The hex number is the length of the parameter block.

A transaction:

1. **Without parameters:** send the header in a `0x2000` frame, reply length 64.
2. **With parameters** (`RESA`, `PARA`): send the header with reply length 0; the
   scanner answers with an empty `0xa000`. Then send the parameter block in its
   own `0x2000` frame with reply length 64.
3. The reply is a 64-byte header `<CMD>x<length>#nrdNONE#…#---`, zero-padded.
4. If the length is > 0, fetch the data block with an empty `0x2000` frame whose
   reply length equals that length.

`#nrdNONE` means ready. Right after reconnecting, the scanner sometimes answers
`#nrdBUSY` with length 0 for about 3 s (each reply delayed by ~1 s); the
command has to be repeated until it reports `NONE`. Observed once directly
after the previous session was released; the exact trigger is unknown.

Parameters and replies are sequences of `#` tokens: a 3-letter name followed by
a value (`d` + 3 decimal digits, `i` + 7 decimal digits, `x` + 7 hex digits,
`h` + 3 hex digits length + string).

| Command | Use |
|---|---|
| `INFO` | device info (`#PRD` product, `#VER` firmware, `#ADFAREA`, …) |
| `CAPA` / `CAPB` | capabilities, front / back side |
| `RESA` | read settings; in `FS Z` e.g. `#ADF` counters, `#SAV` sleep timer |
| `STAT` | status; `#ERRADF PE` = feeder empty |
| `PARA` | set scan parameters |
| `TRDT` | start transfer |
| `IMG ` | poll image data |
| `FIN ` | end of command sequence |

### Session start (as done by Epson Scan and this daemon)

```
connect -> 0x8000 greeting
0x2100 lock -> 0xa100 06
FS X -> 06
INFO, CAPA, STAT
idle: 0x1000 every 60 s, wait for 0x9000
```

## Capabilities of the DS-560

`CAPA` in `FS X` mode:

```
#ADFDPLX#ADFDFL1DFL2#ADFEJCT#ADFOVSN
#COLLISTC024M001M008R001R008G001G008B001B008
#FMTLISTJPG #JPGRANGd001d100#THRRANGd000d255
#GMMLISTUG10UG18#GMTLISTRED GRN BLU MONO#CMXLISTUNITUM08
#RSMLISTi0000300i0000600#RSSLISTi0000300i0000600
```

| Feature | Firmware | Notes |
|---|---|---|
| Duplex | yes | pages arrive as `IMGA` (side facing down) / `IMGB` |
| Double-feed detection | yes, two levels | `DFL1`, `DFL2` |
| Output format | JPEG only, quality 1–100 | no PDF, no raw |
| Page length detection | yes | `#pen` reports the real height |
| Page width cropping | no | width is always the full feeder width (8.5″) |
| Colour / grey / b&w | yes | `C024`, `M008`, `M001` + threshold |
| Colour dropout | yes | single channel `R/G/B 001/008` |
| Resolution | 300 or 600 dpi | |
| Long paper | up to 36″ | `#ADFAMAX…i0003600` |
| Blank page skip, deskew, auto-crop, OCR | **no** | done on the PC by Epson Scan |

`CAPB` (back side) only lists threshold, gamma and gamma tables.

Maintenance mode (`FS Z`) exposes a sleep timer `#SAV` (range 0–120, default
10, presumably minutes) and power-off timer `#POF` (0–480). Writing them was
not tested.

## Scanning

After the button notification:

```
PARA "#ADFDPLX"            -> parOK
STAT
PARA <all settings>        -> parOK
TRDT
IMG … until "#errADF PE"
STAT
```

Example parameter block (600 dpi colour duplex, as sent by Epson Scan, minus
its gamma tables):

```
#ADFDPLXDFL1OVSN#COLC024#FMTJPG #JPGd085#GMMUG18#CMXUNIT
#RSMi0000600#RSSi0000600#ACQi0000000i0000000i0005100i0008400
#BSZi1048576#PAGd000
```

Epson Scan additionally sends three 256-byte tables (`#GMTRED h100…`, `GRN`,
`BLU`) that are almost identity; the scanner accepts the block without them.
`#ACQ` is x, y, width, height in pixels; height is the maximum length, the
firmware stops at the real end of the page.

`IMG ` replies, polled in a loop:

| Reply tokens | Meaning |
|---|---|
| (none, length 0) | nothing ready yet — paper is being fed |
| `#pst<w>i0000000<h>#typIMGA` | page start, front; `h` = requested height |
| `<length>#typIMGA` / `#typIMGB` | data block for that side |
| `#pen<w><h>#typIMGA` | page end; `h` = real height |
| `#errADF PE` | feeder empty, scan finished |
| other `#err…` | error (jam, double feed, …) |

Front and back blocks alternate. A reply with `#typIMGB` and length 0 means the
back side has nothing ready at the moment.

### JPEG quirks

Each page is one baseline JFIF stream, delivered in blocks, but:

- it does **not** end with `ff d9`; the last `ff d9` is followed by padding up
  to a multiple of 64 bytes — cut after the last `ff d9`;
- the SOF segment carries the **requested** height from `#pst`, not the real
  one — patch it with the height from `#pen`.

With that, the JPEGs can be embedded into a PDF unchanged.

### Orientation

Loaded **face down, top edge first**, pages come out upright and in reading
order (`IMGA` = front). Loaded the other way round they are rotated 180°.

## Timing (one sheet, duplex)

| Step | Time after button |
|---|---|
| `TRDT` sent | 0.4 s |
| `TRDT` answered | 0.6 s, sometimes ~11 s (cause unknown; not the sleep timer) |
| first image data | 5–16 s |
| scan complete (600 dpi) | 14–25 s |
