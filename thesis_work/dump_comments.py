import sys, zipfile
from lxml import etree
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ns = {"w": W}
z = zipfile.ZipFile(sys.argv[1])
com = etree.fromstring(z.read("word/comments.xml"))
doc = etree.fromstring(z.read("word/document.xml"))
for c in com.findall("w:comment", ns):
    cid = c.get(f"{{{W}}}id")
    text = "".join(c.itertext())
    # anchored text
    body = doc.find("w:body", ns)
    inside = False
    anchored = []
    para_ctx = None
    for el in body.iter():
        if el.tag == f"{{{W}}}commentRangeStart" and el.get(f"{{{W}}}id") == cid:
            inside = True
            p = el
            while p is not None and p.tag != f"{{{W}}}p":
                p = p.getparent()
            para_ctx = "".join(p.itertext()) if p is not None else ""
        elif el.tag == f"{{{W}}}commentRangeEnd" and el.get(f"{{{W}}}id") == cid:
            inside = False
        elif inside and el.tag == f"{{{W}}}t":
            anchored.append(el.text or "")
    print(f"=== comment {cid} by {c.get(f'{{{W}}}author')}: {text}")
    print("  ANCHOR:", "".join(anchored)[:600])
    print("  PARA:", (para_ctx or "")[:400])
