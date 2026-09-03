#!/usr/bin/env python3
"""
APA matchup analysis — turns pipeline/data/games.csv into per-player analytics that respect
the modeling contract in ANALYSIS-DESIGN.md:
  - Identity is the MEMBER id (mid), stable across seasons. The per-season player id (pid)
    fragments a career into dozens of pieces, so we key everything on mid.
  - SL is time-dependent; never blend SL eras.
  - Descriptive view = full record segmented by the player's OWN SL era.
  - Predictive/lineup view = only games where the player was at their CURRENT SL,
    vs opponents at the target SL (fallback to current SL +/-1 when n < MIN_N).

Emits data/analysis.json consumed by the dashboard.
Run: python3 pipeline/analyze.py [--snapshot]
"""
import csv, json, sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
MIN_N = 5


def load():
    rows = []
    with open(DATA / "games.csv") as f:
        for r in csv.DictReader(f):
            if not r.get("mid") or not r.get("oppMid"):
                continue
            try:
                r["sl"] = int(r["sl"]); r["oppSl"] = int(r["oppSl"])
            except (ValueError, TypeError):
                continue
            if r["sl"] <= 0 or r["oppSl"] <= 0:   # drop forfeits/byes (SL 0)
                continue
            r["win"] = (r["win"] == "True")
            rows.append(r)
    return rows


def current_sl(rows):
    """Most recent game's SL per (member, format) = their current SL."""
    latest = {}
    for r in rows:
        key = (r["mid"], r["fmt"])
        if key not in latest or r["date"] > latest[key][0]:
            latest[key] = (r["date"], r["sl"], r["name"])
    return {k: {"date": v[0], "sl": v[1], "name": v[2]} for k, v in latest.items()}


def vs_sl_table(games):
    by = defaultdict(lambda: {"g": 0, "w": 0})
    for g in games:
        c = by[g["oppSl"]]; c["g"] += 1; c["w"] += 1 if g["win"] else 0
    return [{"vsSL": sl, "games": c["g"], "wins": c["w"],
             "winPct": round(100 * c["w"] / c["g"])} for sl, c in sorted(by.items())]


def analyze_player(mid, fmt, games, cur):
    name = cur.get((mid, fmt), {}).get("name") or games[-1]["name"]
    csl = cur.get((mid, fmt), {}).get("sl")

    traj = []
    for g in sorted(games, key=lambda x: x["date"]):
        if not traj or traj[-1]["sl"] != g["sl"]:
            traj.append({"date": g["date"], "sl": g["sl"]})

    by_era = defaultdict(list)
    for g in games:
        by_era[g["sl"]].append(g)
    descriptive = {str(era): vs_sl_table(gs) for era, gs in sorted(by_era.items())}

    at_current = [g for g in games if g["sl"] == csl]
    predictive = vs_sl_table(at_current)
    near = [g for g in games if csl is not None and abs(g["sl"] - csl) <= 1]
    widened = {t["vsSL"]: t for t in vs_sl_table(near)}
    for cell in predictive:
        if cell["games"] < MIN_N and cell["vsSL"] in widened:
            w = widened[cell["vsSL"]]
            cell["widened"] = {"games": w["games"], "wins": w["wins"], "winPct": w["winPct"]}

    return {"mid": mid, "name": name, "format": fmt, "currentSL": csl,
            "firstSeen": min(g["date"] for g in games) if games else None,
            "lastSeen": max(g["date"] for g in games) if games else None,
            "totalGames": len(games), "wins": sum(1 for g in games if g["win"]),
            "gamesAtCurrentSL": len(at_current),
            "trajectory": traj, "predictiveVsSL": predictive, "descriptiveByEra": descriptive}


def head_to_head(games_by_mid, mid, fmt):
    opp = defaultdict(list)
    for g in games_by_mid[mid]:
        if g["fmt"] == fmt:
            opp[(g["oppMid"], g["oppName"])].append(g)
    out = []
    for (omid, oname), gs in opp.items():
        gs = sorted(gs, key=lambda x: x["date"])
        out.append({"oppMid": omid, "oppName": oname, "meetings": len(gs),
                    "wins": sum(1 for g in gs if g["win"]),
                    "games": [{"date": g["date"], "mySL": g["sl"], "oppSL": g["oppSl"],
                               "won": g["win"]} for g in gs]})
    return sorted(out, key=lambda x: -x["meetings"])


def main():
    rows = load()
    cur = current_sl(rows)
    by_mid_fmt = defaultdict(list); by_mid = defaultdict(list)
    for r in rows:
        by_mid_fmt[(r["mid"], r["fmt"])].append(r); by_mid[r["mid"]].append(r)

    players = {}
    for (mid, fmt), games in by_mid_fmt.items():
        a = analyze_player(mid, fmt, games, cur)
        a["headToHead"] = head_to_head(by_mid, mid, fmt)
        players["%s:%s" % (mid, fmt)] = a

    meta = json.load(open(DATA / "meta.json"))
    out = {"generatedFrom": meta.get("generatedAt"), "memberId": meta.get("memberId"),
           "teams": meta.get("teams"), "playerCount": len(players), "players": players}
    (DATA / "analysis.json").write_text(json.dumps(out))
    print("Wrote analysis.json: %d member-format profiles (from %d games)" % (len(players), len(rows)))

    if len(sys.argv) > 1 and sys.argv[1] == "--snapshot":
        for k, p in players.items():
            if p["name"] and "Timorason" in p["name"]:
                print("\n== %s (%s-ball) — CURRENT SL %s | %d games total, %d at current SL | %s→%s ==" % (
                    p["name"], p["format"], p["currentSL"], p["totalGames"],
                    p["gamesAtCurrentSL"], (p["firstSeen"] or "")[:7], (p["lastSeen"] or "")[:7]))
                print("   trajectory:", " ".join("%s:%s" % (t["date"][:7], t["sl"]) for t in p["trajectory"]))
                print("   PREDICTIVE (at current SL %s only):" % p["currentSL"])
                for c in p["predictiveVsSL"]:
                    w = (" [thin→±1: %d-%d %s%%]" % (c["widened"]["wins"], c["widened"]["games"]-c["widened"]["wins"], c["widened"]["winPct"])) if c.get("widened") else ""
                    print("      vs SL%s: %d-%d (%s%%)%s" % (c["vsSL"], c["wins"], c["games"]-c["wins"], c["winPct"], w))


if __name__ == "__main__":
    main()
