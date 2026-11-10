import sys
from docx import Document
d = Document(sys.argv[1])
body = d.element.body
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
pi = 0; ti = 0
for el in body.iterchildren():
    if el.tag == W + "p":
        from docx.text.paragraph import Paragraph
        p = Paragraph(el, d)
        t = p.text
        has_img = bool(el.findall(".//" + W + "drawing"))
        print(f"P{pi} [{p.style.name}]{' [IMG]' if has_img else ''}: {t}")
        pi += 1
    elif el.tag == W + "tbl":
        from docx.table import Table
        tb = Table(el, d)
        print(f"--- TABLE {ti} ({len(tb.rows)}x{len(tb.columns)})")
        for r in tb.rows:
            print("   | " + " | ".join(c.text.replace("\n", " / ") for c in r.cells))
        ti += 1
