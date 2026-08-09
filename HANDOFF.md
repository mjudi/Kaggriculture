# Kaggriculture agent — session handoff

Context for picking this up in a fresh session with no memory of how it got here.
`main.py` is the current, fully tested, currently-submitted agent. Everything
below is what's already been tried, verified, and learned, so it doesn't get
re-discovered the hard way a second time.

## The competition

Kaggle competition "Kaggriculture" — two-player farming simulation, 720 turns
(24/day x 30 days), $3,000 starting money, board is a 10x10 grid split into
four 5x5 quadrants (only NW unlocked at start). Sponsor Google LLC. Entry/team
deadline Sept 23 2026, final submission Sept 30 2026, leaderboard finalizes
~Oct 15 2026. Prize pool $50,000 across top 10 places ($5,000 each). Only your
latest 2 submissions are tracked for matchmaking and count toward the final
score. Ranking is skill-rating based (win/loss/tie only — margin size does not
affect rating).

Full mechanics (crop tables, market pricing formula, action list) came from
the user's own uploaded rules PDF and README/AGENTS.md early in the session,
cross-checked repeatedly against the actual installed `kaggle_environments`
package source at
`/usr/local/lib/python3.12/dist-packages/kaggle_environments/envs/kaggriculture/kaggriculture.py`
(or wherever it installs locally — `pip install -U kaggle-environments`).
**When in doubt about a mechanic, read that source file directly rather than
trust assumptions** — several real bugs this session came from wrong
assumptions about it (see below).

## Current agent design (main.py)

Single self-contained file, no imports beyond the standard library, no
external files or model weights. Stateless — every decision re-derived from
`obs` each call.

Core loop: farmer + hired hands share one job board each turn (feed > water >
care > crop-decay-urgent > harvest > fertilizer-collect > weed > plant),
claiming distinct targets so units don't collide. Market orders each turn:
throttled selling (tighter caps on glut-prone goods), seed buying, hand
hiring, land buying.

Key tunables and their current values, with the reasoning:

- **Hand scaling**: `target_hands = min(12, 4 + day)` — ramps to the cap by
  day 8 regardless of momentary cash (engine still hires fewer if it
  genuinely can't afford the full request that morning). This replaced an
  earlier money-gated formula (`min(8-12, N + money // X)`) that created a
  self-reinforcing trap: low money kept hands low, which kept money low. A
  24-game batch of real ladder losses showed hand count never breaking 7
  even by day 27 under the old formula, while every strong real opponent
  studied (Xmeeeee, lucaskna, Alex) reached 11-13 hands by day 6-12.
- **Land purchases**: re-enabled, gated on `day >= 4` and tile utilization
  `> 0.7`. Was disabled entirely earlier in the session (land + a low hand
  cap = spreading the same small workforce too thin), then re-enabled once
  hand-scaling was fixed, since land and hands turned out to be strongly
  **complementary, not independently additive** — tested directly: hands-up
  with land off, and land-on with hands still capped low, both individually
  *lost* to the baseline; only scaling both together produced a net gain.
- **Melon prioritized up to `MELON_TARGET = 15`** ahead of even
  diversification for the rest. Informed by three independent sources
  agreeing: a well-verified public notebook's coins-per-action analysis
  (melon far above every other crop, ~250 vs strawberry's ~37, despite
  strawberry's higher base price), a real ladder opponent (`yang20251228`)
  running a pure 15-tile melon monoculture to a big win with only 5 hands
  and no land at all, and a top-leaderboard player (`Liam S.`) beating
  another top-tier strategy specifically by running more melon within an
  otherwise near-identical build. Capped rather than unbounded — melon has a
  steep glut curve, and both the public notebook and the real opponents
  independently converged on roughly this range (4-16 tiles).
- **Crop diversification: CARROT and TOMATO removed entirely from
  `planting_priority`.** Superseded by a real-ladder-informed rewrite (see
  "Real ladder replay analysis" below) — the earlier `CROP_WEIGHT`-weighted
  diversification approach (dividing field count by a per-crop weight)
  is still the mechanism, but the crop *set* changed: only WHEAT
  (bootstrap/animal-feed) and STRAWBERRY/MELON are eligible now.
  `CROP_WEIGHT = {WHEAT: 1, CARROT: 1, TOMATO: 1, STRAWBERRY: 3, MELON:
  1}` (carrot/tomato weights are moot since they're never eligible).
  Verified on 80 real games (two independent 40-seed batches) against
  `main_v1`: **100% win rate (80/0)**, avg margin +$10,448 and +$11,925
  in the two batches respectively. See "Real ladder replay analysis"
  below for the evidence this was based on and the full before/after
  numbers for each change tested in isolation.
- **A real, previously undiagnosed bug fixed: `HIRE` orders were being
  starved by the `maxMarketOrdersPerTurn` cap.** `build_market_orders`
  built one combined list (SELL, then BUY_PRODUCT/BUY_SEED, then up to
  12 HIRE, then BUY_LAND) and truncated the *whole thing* to
  `orders[:10]` — confirmed directly against the engine source
  (`_process_market` truncates each player's entire per-turn order list,
  HIRE included, before processing anything). A real replay showed day
  22 hour 1 issuing 5 SELL + 1 BUY_SEED before reaching any HIRE, leaving
  room for only 4 of the intended 12 that turn — and the observed hand
  count that day was exactly 4. This explains why hand count declined
  late-game despite `target_hands` only going up: more harvest volume
  late game means more SELL orders competing for the same 10 slots. Fix:
  `orders.sort(key=lambda o: 0 if o[0] == "HIRE" else 1)` right before
  the final truncation, so HIRE (cheap, ~$376 for 12 hands, and processed
  atomically — not part of the per-unit lockstep SELL/BUY loop, so
  reordering it is safe) is never crowded out. Verified in isolation: 40
  real games vs `main_v1`, **100% win rate (40/0)**, avg margin +$7,642,
  hand count steady around 10 all game instead of declining to 5-7
  late-game.
- **Animal program: DISABLED, re-tested this session with much stronger
  evidence, still net negative.** `choose_animal_program()` returns
  `None` unconditionally — the logic below it is dead code kept for a
  future attempt. This round's retry was motivated by real replay
  evidence far stronger than what motivated the original attempt (see
  below), and along the way found and fixed a real bug: `BUY_PRODUCT
  WHEAT 10` had no once-per-day gate, so it fired on *every turn* the
  shed was under `WHEAT_FEED_BUFFER` (which stays true for many turns,
  since one 10-unit buy barely dents an 85-unit buffer) — $250/turn,
  repeatedly, crashed one real test game from $1,973 to $176 in three
  in-game days. Fixed by gating it to `hour == 0`, same as HIRE. Even
  after that fix: **animals alone lost 0/40 vs `main_v1` (avg -$17,570)**,
  and **combined with the HIRE fix + crop-mix change, still only 2/38
  (avg -$10,032)** despite hands reliably hitting the target 10/day in
  the combined run — so this isn't a hand-coverage problem being masked,
  animals are a genuine net drag on this economy specifically. Also
  observed: some cow attrition from missed feeding under hand pressure
  (`consecutive_unfed` reaching 2, animal escapes) even with a generous
  wheat buffer — a secondary issue not fully run to ground, since the
  headline result (animals are net negative here) didn't depend on it.
  `ANIMAL_PLAN` was updated to `[("COW", 8), ("SHEEP", 12)]` (up from
  sheep:6) to match this session's real replay data before testing, in
  case a future attempt wants that starting point. See "Real ladder
  replay analysis" below for why this doesn't contradict the strong
  real-world evidence that motivated retrying it — the likely
  explanation is that animals amplify a strong *whole* system rather
  than being independently profitable bolted onto this one.
- **Two real engine bugs found and fixed, worth knowing about for any
  future harvest-priority logic**:
  - Ongoing crops (tomato, strawberry) start decaying about a day after
    hitting their yield cap and convert straight to a weed if left
    unharvested — confirmed directly against the engine's `_decay_plants`
    / `max_lifespan_step` logic. Handled via `crop_maxed()`.
  - One-time crops (wheat, carrot, melon) get a hard decay deadline set
    the moment they're **planted** (`max_lifespan_step = (planted_day +
    max_yield_day + 1) * turns_per_day`), independent of whether/when they
    reach full yield. For melon specifically (10-12 day maturity), that
    leaves only about a 3-day window between full ripeness and decay
    starting. Handled via `crop_urgent()`.
  - Both are wired in at the top of the priority chain, above general
    watering, since a decaying tile is actively losing value, not just
    sitting idle.

## Real ladder data (as of this session)

Two earlier batches of ~24 real games were pulled and analyzed (episode
replays + agent-0 logs downloaded via the Kaggle CLI). No crashes/errors in
any real game so far — every loss has been a genuine strategy gap, not a
bug.

Pattern found in those earlier batches: this agent's own score was fairly
consistent regardless of opponent (roughly $27k-$42k most games). Wins came
easily against weak opponents (some scoring under $6k). Losses came against
a real, sizeable tier of strong opponents scoring $80k-$180k+ — not one
outlier, at least six different named players in that range.

Known strong real opponents studied directly in earlier sessions (useful if
their replays turn up again): `lucaskna` / `somewhere after` (confirmed
literally identical code, 692/720 turns byte-for-byte the same — likely a
shared/forked public notebook, not evidence of multi-accounting) run 8 cow
+ 6 sheep, 12 hands, 3 quadrants, heavy strawberry. `Xmeeeee` runs a
faster-ramping hybrid of melon + modest animals + strawberry.
`yang20251228` runs pure melon monoculture with minimal hands. `Alex` (an
earlier, weaker matchup) showed the original hands+land complementarity
finding.

### Real ladder replay analysis, this session

Pulled 30 of the agent's own real ladder replays (submission `55335956`,
the CROP_WEIGHT-retry version — **16/30 = 53.3% real win rate**, much lower
than the 93.8% seen locally against `main_v1` at the time, confirming
local single-opponent testing wasn't representative of the real field) and
10 replays of a specific real top-10 leaderboard player, "Seb (allegedly)"
(8/10 in this sample — consistently strong). Replays extracted from Kaggle
zip downloads (`env.toJSON()`-format episode JSON, same schema as
`replay.json` from local testing) and parsed with a scratch script
(`_analyze_replays.py`, not kept in the repo long-term — reconstructable
from this description if needed again: sample each game at a mid-day step
offset, `day*24 + 12`, not `day*24 + 0`, since hour 0 is right after the
daily hand-reset and reads as 0 hands even when hiring works correctly).

Money trajectories between the two players were nearly identical through
day 14 (~$2,500 each on average), then Seb's exploded 10x by day 22 while
this agent's barely tripled. Three concrete, evidence-backed gaps were
found, all described in the "Current agent design" bullets above with
their fix and verified results:

1. Seb ran a full animal program (~8 cow + ~12 sheep) in **all 10** of his
   games, sustained from day 14 onward, with his money curve inflecting
   sharply right when the herd hit full scale.
2. Seb planted **zero carrot and zero tomato, ever**, across all 10 games
   — strawberry-dominant (peaks ~41-43 tiles around day 14-18, wound down
   in a liquidation-like pattern toward day 29) with melon as an early
   secondary crop.
3. A real, previously undiagnosed bug (HIRE orders starved by the
   10-order market cap) was found by noticing this agent's own hand count
   *declining* late-game despite the scaling formula only going up.

Testing each of the three fixes in isolation (methodology below) showed
#2 and #3 were strong, real, verified wins, while #1 (animals) was **not**
— it lost badly alone and even combined with the other two fixes. This
doesn't mean the replay observation was wrong; it means Seb's animal
program most likely works *because of* something else in his build this
agent still doesn't replicate (very high land/tile utilization — ~43
strawberry + ~12 melon + ~18-19 pasture tiles is close to the full
100-tile, 4-quadrant footprint — or some other systemic difference), not
because animals are independently profitable bolted onto an otherwise
similar economy. Real replay evidence is strong for *what a winning
opponent does*, but not proof that any single piece of their build is
independently causal — this is the same lesson as the CROP_WEIGHT
regression earlier in the project, from the opposite direction: there, a
single replay wrongly generalized into a bad change; here, ten replays
correctly pointed at two real wins and one real dead end, but only
because each was actually isolated and tested rather than assumed.

**A costly methodology lesson from this session, worth avoiding next
time:** when testing multiple variants of `main.py` by copying files
around, a background batch test that reads `"main.py"` as a **file path
string** re-reads the file from disk on *every single game*, not once at
the start. Overwriting `main.py` while a background test is still running
against it silently corrupts the results with a mix of old/new code —
this produced a nonsense "5% win rate" for a change that a clean rerun
showed was actually 100%. Fix: give every variant under test its own
permanent, never-overwritten filename (e.g. `main_fix1_only.py`) and
never touch a file while any test might still be reading it. A copy
immediately after finishing an edit, before running anything else, is the
safest habit.

## Testing workflow

`kaggle-environments` is a real pip package (`pip install -U
kaggle-environments`) with `kaggriculture` as a registered environment —
this session had it installed locally and used it directly for everything,
not just theorized. Built-in opponents: `"pass"`, `"random"`, `"starter"`.

Standard verification battery used all session, worth keeping: single-seed
sanity check first, then a real batch (20-40 seeds) against `main_v1.py`
(the user's own earlier, independently-built strong reference agent —
should still be in the repo/notebook history) reporting win/loss count +
average margin + worst/best margin (not just average — a version that wins
bigger but less often can be a worse move for actual ladder rating, which
only counts win/loss/tie), a batch against `starter` and `random` to catch
regressions, and a self-play run (`env.run([agent, agent])`, check both
sides finish `DONE`) as cheap insurance against burning a real submission
slot on an error Kaggle's own validation episode would otherwise catch.

**Every "obviously should help" idea this session that wasn't tested this
way ended up wrong or incomplete at least once** — treat that as the actual
operating principle here, not a one-off caution.

## Open threads / natural next steps

1. **Submitted 2026-08-07** — the CROP_WEIGHT-retry `main.py` (hand-scaling
   fix + conservative CROP_WEIGHT diversification, see above) went in as
   submission ID `55335956`. **Superseded by this session's changes below
   before real ladder data came back on it** — the 16/30 real result
   analyzed this session was on that submission, and directly motivated
   the HIRE-fix and crop-mix changes now in `main.py`. Not yet
   resubmitted as of this writing (see below).
2. **Not yet submitted to Kaggle**: `main.py` now has the HIRE-order-
   starvation fix and the carrot/tomato-dropped, strawberry-dominant crop
   mix, verified locally at 100% (80/0) across two independent 40-seed
   batches vs `main_v1`, avg margins +$10,448 and +$11,925. This is the
   strongest fully-verified local result of the project so far and is
   ready for the next submission slot, pending the user's explicit
   go-ahead per the established submission workflow (join/rules-accepted
   check, self-play sanity, then `kaggle competitions submit`).
3. Once submitted, get a fresh, large (20+ game) batch of real ladder
   replays on the new submission and re-run the same win/loss +
   day-by-day trajectory analysis used this session (money, hand count,
   crop mix, animal presence, sampled at `day*24 + 12` per game) to
   confirm the local gains hold up against the real field — this
   methodology (comparing your own replays against a specific strong
   opponent's replays, not just aggregate win rate) is what surfaced
   the two real fixes this session and is worth repeating as a matter of
   course, not just when stuck.
4. The animal program remains a real, correctly-built, currently-dead
   feature. This session's retry was informed by much stronger evidence
   than the original attempt (10 real replays of one consistently
   successful player, vs a single replay before) and still lost, even
   after fixing a real bug found along the way (see above) — so this
   isn't an open question about whether the mechanism works, it's now
   fairly well established that animals don't help *this specific
   economy* even when it's the stronger, crop-mix-fixed version. Only
   worth another look if some other, more fundamental gap against Seb's
   build gets closed first (land/tile utilization is the leading
   candidate — his ~75+ tiles in active use is close to the full
   100-tile footprint, well above what this agent currently achieves).
5. Melon's `MELON_TARGET = 15` was left unchanged this round — Seb's
   real melon peak (13-18 tiles) already falls inside that range, so no
   evidence surfaced this session to revise it. Worth another look if a
   future replay batch shows otherwise.
