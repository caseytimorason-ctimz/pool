#!/usr/bin/env python3
"""
Hudson County bylaws PDF -> structured sections in data/league.json.

Kept as a script rather than a one-off paste so a reissued bylaws PDF is a re-run, not a
retranscription. Two things the PDF does that have to be undone: it hard-wraps sentences
mid-line (so lines are rejoined into paragraphs), and it draws bullets with a Wingdings
private-use glyph (U+F0B7) that no bullet regex would recognise.

The playoff-structure grid is NOT parsed — it is a multi-column table that any text
extractor shreds — and stays hand-transcribed in league.json under playoffStructure.
"""
import json, re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "reference" / "hudson-county-bylaws-2025.pdf"
TXT = ROOT / "reference" / "hudson-county-bylaws-2025.txt"
OUT = ROOT / "data" / "league.json"

BULLET = "●"
PUA = {"": BULLET, "": BULLET, "•": BULLET}
# Headings the PDF sets in Title Case rather than caps. Without these they read as body text
# and their rules get swallowed into the previous section.
TITLECASE = {"Introduction", "Age Requirements", "Office Hours", "Website",
             "World Qualifier", "Travel Assistance for the World Pool Championships"}
RENAME = {"LOCAL BYLAWS": "League office"}


def clean(l):
    for k, v in PUA.items():
        l = l.replace(k, v)
    return re.sub(r"\s{2,}", " ", l.strip())


def junk(l):
    t = l.strip()
    return (not t) or bool(re.fullmatch(r"\d{1,2}", t)) or t.startswith("pdeeken ,")


def is_head(l):
    t = l.strip()
    if not t or len(t) > 70:
        return False
    if re.match(r"^Section \d+:", t) or t in TITLECASE:
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z0-9 &(),./:'’-]{3,}", t))


def paragraphs(body):
    """Rejoin hard-wrapped lines; keep each bullet/numbered item as its own paragraph."""
    out, buf = [], ""
    for l in body:
        if re.match(r"^(%s|\d+[.)]|[a-z][.)])\s*" % BULLET, l):
            if buf:
                out.append(buf)
            buf = l
        elif not buf:
            buf = l
        else:
            buf += " " + l
    if buf:
        out.append(buf)
    # Prize/fee lists run "1st place: … 2nd place: …" on one wrapped line; break them apart
    # so each place reads as its own item instead of a wall of dollar figures.
    split = []
    for p in out:
        # longest alternative first, or "3rd + 4th place" splits at the bare "3rd"
        parts = re.split(r"(?=\b(?:3rd \+ 4th|1st|2nd|3rd|4th) place:)|(?=\*[A-Z])", p)
        split += [x.strip() for x in parts if x.strip()]
    # "3rd + 4th place:" is hard-wrapped after the "+", so the split above lands between them.
    merged = []
    for p in split:
        if merged and merged[-1].endswith("+"):
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


def main():
    if not TXT.exists() or PDF.stat().st_mtime > TXT.stat().st_mtime:
        subprocess.run(["pdftotext", "-layout", str(PDF), str(TXT)], check=True)

    secs, cur = [], None
    for raw in TXT.read_text().split("\n"):
        if junk(raw):
            continue
        l = clean(raw)
        if is_head(l):
            cur = {"title": RENAME.get(l.rstrip(":"), l.rstrip(":")), "body": []}
            secs.append(cur)
            continue
        if cur is None:
            cur = {"title": "Introduction", "body": []}
            secs.append(cur)
        cur["body"].append(l)

    out = []
    for s in secs:
        # the playoff grid's shredded fragments start here and are replaced by the hand
        # transcription in playoffStructure
        b = [p for p in paragraphs(s["body"]) if not p.startswith("Number Of Teams")]
        if b:
            out.append({"title": s["title"], "body": b})

    lg = json.loads(OUT.read_text())
    lg["bylaws"]["sections"] = out
    OUT.write_text(json.dumps(lg, indent=2, ensure_ascii=False))
    print("wrote %d sections (%d paragraphs)" % (out.__len__(), sum(len(s["body"]) for s in out)))


if __name__ == "__main__":
    main()
