# Rack Sheet — APA Pool Matchup Console

Private tool for our APA teams: point it at an upcoming opponent and it ranks each of our
players against theirs, plus explore any player's 8-year skill-level history and head-to-heads.

**Dashboard:** `index.html` (self-contained; league data embedded at build time).
Connect this repo to Netlify — publish directory `.`, no build command.

## Matchup engine
For each pairing (our A vs their Q) it blends, weighted by sample size:
1. League baseline — empirical win rate for A's SL vs Q's SL across all our matches.
2. A's edge vs that SL — using only A's games at A's *current* SL (SL is time-dependent).
3. Direct head-to-head A vs Q (by member id), if any.
Thin evidence falls back to baseline and is flagged low-confidence. Full spec: ANALYSIS-DESIGN.md.

## Weekly refresh (laptop)
Data via APA's GraphQL API, headless auth with a refresh token in macOS Keychain
(service `apa-refresh-token`) — no secrets in this repo.
```
python3 pipeline/apa_pull.py && python3 pipeline/analyze.py && python3 pipeline/build_site_data.py
python3 - <<'PY'
open('index.html','w').write(open('pipeline/template.html').read().replace('__APA_DATA__',open('data.json').read()))
PY
git add -A && git commit -m "weekly refresh" && git push
```
