# APA Matchup — Analysis Design (the modeling contract)

## Core principle: skill level is TIME-DEPENDENT. Never blend SL eras.
Every game stores the SL BOTH players held at that moment (`MatchScore.skillLevel` for each
side). A lifetime record blends a player's SL3 era with their SL7 era and misrepresents the
present. So the tool separates two questions:

### 1. Descriptive view — "the player's story" (uses ALL games, segmented by era)
- SL trajectory timeline (SL by session; e.g. Casey 8-ball 5→4→6→5→6, 9-ball 6→7).
- Record vs each opponent SL, **grouped by the player's OWN SL at the time**
  (how they did vs SL4s while they were a 5, vs while they were a 6, etc.).

### 2. Predictive view — "who do we play this week" (the LINEUP tool)
- Question: teammate X is SL_now; opponent Y is SL_now. How does X-at-SL_now do vs SL(Y_now)?
- **Default filter: only games where X was at their CURRENT SL**, vs opponents at the target SL.
- Fallback when current-SL samples are thin (<~5 games): widen to current SL ±1, clearly flagged,
  and show the n so the user knows how much to trust it.
- Opponents drift too: a named head-to-head ("X vs Rival Y") must stamp each meeting with BOTH
  SLs at the time; for prediction, weight meetings matching both players' CURRENT SLs.

## Per-player outputs
1. SL-trajectory timeline (season → SL, per format).
2. Current-SL matchup grid: record vs each opponent SL, restricted to current-SL games (n shown).
3. Head-to-head cards vs named rivals: each meeting with date + both SLs + result.
4. Efficiency context where useful (innings, points/match, break-and-runs) — secondary to W/L.

## Data honesty rules (carry into the UI)
- Always show sample size (n) next to any win %; thin cells get a visible low-confidence marker.
- Distinguish `MatchScore.skillLevel` (SL at time of game — the analysis axis) from
  `Player.skillLevel` (current — the filter target).
- 8-ball and 9-ball are different games/scoring — never pool them; the SL scales differ too.
- Forfeits / doubles / incomplete games flagged and excluded from skill inference by default.

## Deployment (decided 2026-09-02)
- Personal GitHub repo + personal Netlify (NOT Casey's SP org / work accounts). Fully separate.
- Weekly scheduled run on Casey's PERSONAL LAPTOP (this Mac) via launchd — for the ephemeral
  Scorekeeper granular capture + refreshing the dataset. Data pull is headless (Keychain refresh
  token, no browser).

---

# THE MATCHUP ENGINE (centerpiece)

Point it at an opponent team → rank each of our players against each of theirs → recommend a lineup.

## Inputs
- Our roster (current SLs) + our players' full history (have it).
- Opponent roster + current SLs — from the upcoming match page or `team(id){ roster }`.
- The shared history pool: 4,388 games / 1,910 players over 8 years — many opponents are
  already in it, so direct history often exists.

## Per-pairing expected win probability  P(our player A beats their player Q)
Blend of signals, each gated by sample size (show the n and a confidence marker):
1. **Race-chart baseline.** APA handicaps every match by SL (higher SL must win more games),
   so a pairing is ~50/50 by design. Compute the LEAGUE-WIDE empirical win rate for each
   SL-vs-SL cell from our 4,388 games — this is the honest baseline, and it already encodes
   which SL pairings slightly favor which side in practice.
2. **A's edge vs this SL.** A's win% vs opponents at Q's current SL, using ONLY A's games at
   A's CURRENT SL (the predictive view). This is "does A over/under-perform the race vs SL_Q."
3. **Direct head-to-head.** A vs Q by member id, recency-weighted, each meeting stamped with
   the SLs at the time (down-weight meetings at very different SLs than today).
4. **Corroboration.** Q's win% vs SL_A (from Q's side, if Q is in our data).
Weight by evidence: strong direct H2H > A-vs-SL sample > league baseline. Thin everywhere →
fall back to the baseline and SAY SO.

## Outputs
- **Matchup grid:** our players (rows) × their players (cols), each cell = P(win) + n + confidence.
- **8-ball (put-up/counter format):** for each opponent player, our best counter and why
  ("A is 7-2 vs SL4s at his current SL; also 2-0 vs Q directly in 2025-26").
- **9-ball / preset lineups:** optimal ASSIGNMENT maximizing total expected wins across the
  lineup (Hungarian algorithm), plus the expected points spread.
- **Team summary:** our strongest and most dangerous matchups; where we're outgunned.
- Always surface uncertainty: a recommendation on n=2 is flagged, not hidden.

## Honesty guardrails
- Never present a blended lifetime number; the engine runs on current-SL data by construction.
- A pairing with no direct history and thin A-vs-SL data must show the league baseline + low
  confidence, not a false-precision percentage.
- 8-ball and 9-ball engines are separate (different handicap math and SL scales).
