"""Tests für Wire-Format, Scan-Ablauf und PDF — nachgestellt aus den Mitschnitten."""

from __future__ import annotations

import io
import re
import subprocess

import pytest
from PIL import Image

from ds560 import protocol as p
from ds560.pdf import write_pdf
from ds560.scan import ScanSettings, finish_jpeg, parse_dims, parse_tokens, run_scan


def make_jpeg(w: int, h: int, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def as_firmware(jpeg: bytes, requested_h: int) -> bytes:
    """So liefert der DS-560: SOF-Höhe = angefragte Höhe, Füllbytes bis Vielfaches von 64."""
    data = bytearray(jpeg)
    i = data.find(b"\xff\xc0")
    data[i + 5 : i + 7] = requested_h.to_bytes(2, "big")
    pad = (-len(data)) % 64 or 64
    return bytes(data) + bytes([0x00, 0xA2, 0x80, 0x16]) * (pad // 4)


# --- Wire-Format: Bytes exakt wie in startup.pcapng ------------------------
def test_encode_matches_capture():
    lock = p.encode_frame(p.TYPE_LOCK, p.LOCK_PAYLOAD)
    assert lock == bytes.fromhex("49532100000c000000070000" "01a0040000012c")
    info = p.encode_data(p.esci2_header(p.CMD_INFO), 64)
    assert info == bytes.fromhex("49532000000c000000140000" "0000000c00000040") + b"INFOx0000000"
    assert p.encode_data(b"", 0x50) == bytes.fromhex("49532000000c000000080000" "0000000000000050")
    assert p.esci2_header(p.CMD_PARA, 0x3AC) == b"PARAx00003AC"


def test_parse_reply_header():
    r = p.parse_reply_header(b"IMG x0001880#psti0005100i0000000i0008872#typIMGA#---" + b"\0" * 12)
    assert (r.cmd, r.data_len) == (b"IMG ", 0x1880)
    tok = parse_tokens(r.tokens)
    assert tok["typ"] == b"IMGA" and parse_dims(tok["pst"]) == (5100, 8872)


def test_para_matches_capture_without_gamma_tables():
    assert ScanSettings().para() == (
        b"#ADFDPLXDFL1OVSN#COLC024#FMTJPG #JPGd085#GMMUG18#CMXUNIT"
        b"#RSMi0000600#RSSi0000600#ACQi0000000i0000000i0005100i0008400#BSZi1048576#PAGd000")


# --- JPEG-Korrektur --------------------------------------------------------
def test_finish_jpeg_restores_original():
    jpeg = make_jpeg(64, 40)
    fixed = finish_jpeg(as_firmware(jpeg, 300), 40)
    assert fixed == jpeg
    assert Image.open(io.BytesIO(fixed)).size == (64, 40)


# --- Scan-Ablauf mit nachgespieltem Scanner --------------------------------
class FakeLink:
    """Spielt die IMG-Antworten eines Duplex-Blatts wie in button.pcapng ab."""

    def __init__(self, front: bytes, back: bytes):
        half = len(front) // 2
        self.img = [
            (b"", b""),                                  # Einzug läuft an
            (b"#psti0000064i0000000i0000300#typIMGA", front[:half]),
            (b"#psti0000064i0000000i0000300#typIMGB", b""),
            (b"#typIMGA", front[half:]),
            (b"#typIMGB", b""),                          # Rückseite noch nicht bereit
            (b"#typIMGB", back),
            (b"#peni0000064i0000040#typIMGA", b""),
            (b"#peni0000064i0000040#typIMGB", b""),
            (b"#errADF PE  ", b""),
        ]
        self.sent = []

    def esci2(self, cmd, params=b""):
        self.sent.append((cmd, params))
        if cmd == p.CMD_IMG:
            tokens, data = self.img.pop(0)
        elif cmd == p.CMD_PARA:
            tokens, data = b"#nrdNONE#parOK  ", b""
        else:
            tokens, data = b"#nrdNONE", b""
        return p.Reply(cmd, len(data), tokens), data


def test_run_scan_duplex(monkeypatch):
    monkeypatch.setattr("ds560.scan.IMG_POLL_INTERVAL", 0)
    front, back = make_jpeg(64, 40, (255, 0, 0)), make_jpeg(64, 40, (0, 0, 255))
    link = FakeLink(as_firmware(front, 300), as_firmware(back, 300))
    pages = run_scan(link, ScanSettings())
    assert [pg.side for pg in pages] == ["IMGA", "IMGB"]
    assert [pg.jpeg() for pg in pages] == [front, back]
    assert [c for c, _ in link.sent[:4]] == [p.CMD_PARA, p.CMD_STAT, p.CMD_PARA, p.CMD_TRDT]


# --- PDF -------------------------------------------------------------------
def test_write_pdf(tmp_path):
    jpeg = make_jpeg(600, 300)
    out = tmp_path / "t.pdf"
    write_pdf(out, [(jpeg, 600, 300), (jpeg, 600, 300)], dpi=600)
    data = out.read_bytes()
    assert data.count(jpeg) == 2  # unverändert eingebettet
    info = subprocess.run(["pdfinfo", str(out)], capture_output=True, text=True)
    if info.returncode == 127:
        pytest.skip("pdfinfo fehlt")
    assert info.returncode == 0, info.stderr
    assert re.search(r"Pages:\s+2\n", info.stdout)
    assert "72 x 36 pts" in info.stdout


# --- Konfiguration ---------------------------------------------------------
def test_config_settings_and_validation():
    from ds560.config import ConfigError, scan_settings

    s = scan_settings({"resolution": 600, "color": "gray", "duplex": False, "double_feed": 2,
                       "overscan": False})
    assert s.para().startswith(b"#ADFDFL2#COLM008#FMTJPG ")
    assert b"#RSMi0000600" in s.para()
    assert b"#THRd080" in scan_settings({"color": "mono", "threshold": 80}).para()
    for bad in ({"resolution": 400}, {"color": "bunt"}, {"jpeg_quality": 0}, {"duplex": "ja"}):
        with pytest.raises(ConfigError):
            scan_settings(bad)
