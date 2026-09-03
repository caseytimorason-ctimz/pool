#!/usr/bin/env python3
"""
template.html -> index.html (data embedded) + index_repo.html (data fetched).

One template, two outputs. index.html carries the scoped bundle inline so it works as a
single file with no server — that's the version that gets published as an artifact and the
one a teammate can save. index_repo.html leaves the data out and fetches ./data.json at load,
which is what Netlify serves so the full league-wide bundle isn't parsed on every page view.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
MARK = "__APA_DATA__"


def main():
    tpl = (SITE / "template.html").read_text()
    if MARK not in tpl:
        raise SystemExit("template.html is missing the %s placeholder" % MARK)

    data = (SITE / "data.json").read_text()
    (SITE / "index.html").write_text(tpl.replace(MARK, data))
    (SITE / "index_repo.html").write_text(tpl.replace(MARK, "null"))
    for f in ("index.html", "index_repo.html"):
        print("%-18s %6.2f MB" % (f, (SITE / f).stat().st_size / 1024 / 1024))


if __name__ == "__main__":
    main()
