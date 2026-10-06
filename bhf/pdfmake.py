"""A one-page PDF record for documents NetSuite can't print (stock issues / inventory adjustments).

Plain PDF 1.4 written by hand with the built-in Helvetica / Courier fonts, so there's no dependency.
"""


def _esc(s) -> str:
    s = str(s if s is not None else "").encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def record_pdf(title: str, fields: list[tuple[str, str]], columns: list[tuple[str, int]], rows: list[list],
               footer: str = "") -> bytes:
    """title, then "label: value" lines, then a fixed-width table (columns = [(heading, width chars)])."""
    ops, y = [], 800

    def text(x, y_, s, font="F1", size=10):
        ops.append(f"BT /{font} {size} Tf {x} {y_} Td ({_esc(s)}) Tj ET")

    text(50, y, title, "F2", 16)
    y -= 28
    for label, value in fields:
        text(50, y, f"{label}:", "F2", 10)
        text(205, y, value)
        y -= 15
    y -= 12

    def row(cells, font):
        line = "".join(str(c if c is not None else "")[:w - 1].ljust(w) for c, (_, w) in zip(cells, columns))
        text(50, y, line, font, 8.5)

    row([h for h, _ in columns], "F4")
    y -= 4
    ops.append(f"50 {y} m 545 {y} l S")
    y -= 12
    for r in rows:
        if y < 70:
            break
        row(r, "F3")
        y -= 13
    if footer:
        text(50, 40, footer, "F1", 7.5)

    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << "
        b"/F1 5 0 R /F2 6 0 R /F3 7 0 R /F4 8 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
