"""Minimaler PDF-Schreiber: JPEG-Seiten unverändert (DCTDecode) in ein PDF.

Keine Neukodierung, keine Abhängigkeiten. OCR und PDF/A erzeugt später
Paperless-ngx aus dieser Datei.
"""

from __future__ import annotations

import io
from pathlib import Path


def _jpeg_components(jpeg: bytes) -> int:
    """Anzahl Farbkomponenten aus dem SOF-Segment (1 = Grau, 3 = RGB)."""
    i = 2
    while i + 4 < len(jpeg) and jpeg[i] == 0xFF:
        marker, seglen = jpeg[i + 1], int.from_bytes(jpeg[i + 2 : i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            return jpeg[i + 9]
        i += 2 + seglen
    return 3


def write_pdf(path: Path, pages: list[tuple[bytes, int, int]], dpi: int) -> None:
    """Schreibt ``pages`` = [(jpeg, breite_px, höhe_px), …] als PDF nach ``path``."""
    out = io.BytesIO()
    offsets: list[int] = []

    def obj(body: bytes, stream: bytes | None = None) -> None:
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % len(offsets) + body)
        if stream is not None:
            out.write(b"\nstream\n" + stream + b"\nendstream")
        out.write(b"\nendobj\n")

    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    n = len(pages)
    # Objekte: 1 Katalog, 2 Seitenbaum, dann je Seite: Seite, Bild, Inhalt
    page_ids = [3 + 3 * i for i in range(n)]
    obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % pid for pid in page_ids), n))
    for i, (jpeg, w, h) in enumerate(pages):
        pid = page_ids[i]
        pw, ph = w * 72 / dpi, h * 72 / dpi
        cs = b"/DeviceGray" if _jpeg_components(jpeg) == 1 else b"/DeviceRGB"
        content = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q" % (pw, ph)
        obj(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
            b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
            % (pw, ph, pid + 1, pid + 2))
        obj(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
            b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>" % (w, h, cs, len(jpeg)),
            jpeg)
        obj(b"<< /Length %d >>" % len(content), content)

    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%EOF\n"
              % (len(offsets) + 1, xref))
    path.write_bytes(out.getvalue())
