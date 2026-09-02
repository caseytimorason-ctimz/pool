#!/usr/bin/env python3
"""
APA matchup engine — rank our players vs an opponent team's roster and suggest a lineup.

P(our A beats their Q) blends, weighted by evidence (see ANALYSIS-DESIGN.md):
  1. League baseline: empirical win rate for (A.currentSL vs Q.currentSL) across all our games.
  2. A's edge vs Q's SL: A's win rate vs that SL, using only A's games at A's CURRENT SL.
  3. Direct head-to-head A vs Q (by member id), recency- & SL-context-weighted.
Thin evidence falls back toward the baseline and is flagged with a confidence marker.

Usage: python3 pipeline/matchup.py <ourTeamId> <oppTeamId> <fmt 8|9>
"""
import csv, json, sys, sqlite3, urllib.request, urllib.error, math, os
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LS = (Path.home() / "Library/Containers/league.poolplayers.com/Data/Library/WebKit/WebsiteData"
      "/Default/ORyOecTgL3hb22UNOIkn7DC33SbJKxT_s3tjRtJIy4A"
      "/ORyOecTgL3hb22UNOIkn7DC33SbJKxT_s3tjRtJIy4A/LocalStorage/localstorage.sqlite3")
K_SL, K_H2H = 8.0, 3.0  # shrinkage: how many games before a signal fully overrides the baseline


def token():
    rt = sqlite3.connect(str(LS)).execute(
        "SELECT value FROM ItemTable WHERE key='refreshToken'").fetchone()[0]
    rt = (rt.decode("utf-16-le") if isinstance(rt, (bytes, bytearray)) else str(rt)).strip().strip('"')
    return _post("mutation($rt:String!){generateAccessToken(refreshToken:$rt){accessToken}}",
                 {"rt": rt})["data"]["generateAccessToken"]["accessToken"]


def _post(q, v=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = tok
    r = urllib.request.Request("https://gql.poolplayers.com/graphql",
                               data=json.dumps({"query": q, "variables": v or {}}).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return json.load(x)
    except urllib.error.HTTPError as e:
        return json.load(e)


def roster(team_id, tok):
    d = _post("query($id:Int!){ team(id:$id){ id name roster { id displayName skillLevel "
              "member { id } } } }", {"id": team_id}, tok)
    t = (d.get("data") or {}).get("team") or {}
    out = []
    for p in (t.get("roster") or []):
        m = p.get("member") or {}
        if p.get("skillLevel"):
            out.append({"mid": m.get("id"), "name": p.get("displayName"), "sl": p["skillLevel"]})
    return t.get("name"), out


def league_baseline(fmt):
    """Empirical P(win) for (mySL vs oppSL) across all our games in this format."""
    tab = defaultdict(lambda: {"g": 0, "w": 0})
    with open(DATA / "games.csv") as f:
        for r in csv.DictReader(f):
            if r["fmt"] != fmt or not r["sl"] or not r["oppSl"]:
                continue
            try:
                a, b = int(r["sl"]), int(r["oppSl"])
            except ValueError:
                continue
            if a <= 0 or b <= 0:
                continue
            c = tab[(a, b)]; c["g"] += 1; c["w"] += 1 if r["win"] == "True" else 0
    return {k: (v["w"] / v["g"], v["g"]) for k, v in tab.items()}


def score(A, Q, fmt, analysis, base):
    """Return dict with P(win) and the evidence behind it."""
    b, bn = base.get((A["sl"], Q["sl"]), (0.5, 0))
    p = b; parts = ["base %.0f%% (n=%d)" % (b * 100, bn)]

    prof = analysis["players"].get("%s:%s" % (A["mid"], fmt))
    # A's record vs Q's SL at A's current SL
    if prof:
        cell = next((c for c in prof["predictiveVsSL"] if c["vsSL"] == Q["sl"]), None)
        if cell and cell["games"]:
            w_s, n_s = cell["wins"] / cell["games"], cell["games"]
            wt = n_s / (n_s + K_SL)
            p = (1 - wt) * p + wt * w_s
            parts.append("vs SL%d: %d-%d" % (Q["sl"], cell["wins"], cell["games"] - cell["wins"]))
        # direct H2H vs Q
        h2h = next((h for h in prof["headToHead"] if h["oppMid"] == Q["mid"]), None) if prof else None
        if h2h and h2h["meetings"]:
            w_h, n_h = h2h["wins"] / h2h["meetings"], h2h["meetings"]
            wt = min(0.6, n_h / (n_h + K_H2H))  # cap H2H influence
            p = (1 - wt) * p + wt * w_h
            parts.append("H2H %d-%d" % (h2h["wins"], h2h["meetings"] - h2h["wins"]))
    ev = (cell["games"] if prof and cell else 0) + (h2h["meetings"] if prof and h2h else 0)
    conf = "high" if ev >= 12 else "med" if ev >= 5 else "low"
    return {"p": p, "conf": conf, "why": " | ".join(parts)}


def main():
    our_id, opp_id, fmt = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    tok = token()
    our_name, ours = roster(our_id, tok)
    opp_name, theirs = roster(opp_id, tok)
    analysis = json.load(open(DATA / "analysis.json"))
    base = league_baseline(fmt)

    print("MATCHUP ENGINE — %s vs %s (%s-ball)\n" % (our_name, opp_name, fmt))
    print("Our roster:  " + ", ".join("%s(SL%d)" % (p["name"], p["sl"]) for p in ours))
    print("Their roster:" + ", ".join("%s(SL%d)" % (p["name"], p["sl"]) for p in theirs))

    # best counter for each of their players
    print("\n=== BEST COUNTER per opponent ===")
    for Q in sorted(theirs, key=lambda x: -x["sl"]):
        ranked = sorted(((A, score(A, Q, fmt, analysis, base)) for A in ours),
                        key=lambda t: -t[1]["p"])
        A, s = ranked[0]
        print(" vs %s (SL%d): put up %s (SL%d) — %.0f%% [%s]  {%s}" % (
            Q["name"], Q["sl"], A["name"], A["sl"], s["p"] * 100, s["conf"], s["why"]))

    # team overview: our players' avg expected win prob across their roster
    print("\n=== OUR PLAYERS ranked by avg expected win vs this team ===")
    rows = []
    for A in ours:
        ps = [score(A, Q, fmt, analysis, base)["p"] for Q in theirs]
        rows.append((A, sum(ps) / len(ps)))
    for A, avg in sorted(rows, key=lambda t: -t[1]):
        print("  %-24s SL%d  avg %.0f%%" % (A["name"], A["sl"], avg * 100))


if __name__ == "__main__":
    main()
