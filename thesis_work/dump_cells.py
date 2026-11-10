import sys
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
d = Document(sys.argv[1])
t = d.tables[0]
seen = set()
for ri, row in enumerate(t.rows):
    for ci, cell in enumerate(row.cells):
        if id(cell._tc) in seen:
            continue
        seen.add(id(cell._tc))
        print(f"######## CELL r{ri} c{ci}")
        pi = 0
        for el in cell._tc.iterchildren():
            if el.tag == W + "p":
                p = Paragraph(el, cell)
                img = " [IMG]" if el.findall(".//" + W + "drawing") else ""
                cm = " [CMT]" if el.findall(".//" + W + "commentRangeStart") else ""
                print(f"p{pi} [{p.style.name}]{img}{cm}: {p.text}")
                pi += 1
            elif el.tag == W + "tbl":
                tb = Table(el, cell)
                print(f"  --- NESTED TABLE ({len(tb.rows)}x{len(tb.columns)})")
                for r in tb.rows:
                    print("    | " + " | ".join(c.text.replace("\n", " / ") for c in r.cells))
