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
- **Animal program: RE-ENABLED at a small, deliberately conservative
  scale (`ANIMAL_PLAN = [("COW", 4)]`, no sheep, no goose) — this took
  two full rounds of testing this session to get right, in this order:**
  1. First retry, matching real replay scale directly (`[("COW", 8),
     ("SHEEP", 12)]`, informed by two independent top-10 leaderboard
     players' replays — see "Real ladder replay analysis" below): lost
     0/40 vs the same agent with animals off (avg -$26,986), even after
     fixing a real feed-coordination bug (see below) and loosening the
     purchase-pacing gate. Herd visibly oscillated (e.g. 8→3→5→8 cows
     over ten in-game days) instead of holding steady the way the real
     replays showed.
  2. Second retry, small scale (`[("COW", 4)]` only): **holds steady at
     4/4 from ~day 14 onward with no oscillation**, and wins decisively
     against the same agent with animals off — 90.0% (36/4, avg
     +$13,374) and 80.0% (32/8, avg +$10,608) on two independent 40-seed
     batches, **100% (40/0, avg +$34,072) vs `main_v1`** — the strongest
     verified result of the whole project. Confirms the underlying
     mechanism is genuinely good; the earlier "animals lose every time"
     conclusion (both this session's first retry and last session's)
     was a *scale* problem, not a fundamentally bad idea — this
     codebase's hand-coordination can reliably sustain a small herd but
     not a large one (yet).
  - **A real, previously undiagnosed feed-coordination bug, found and
    fixed along the way**: a unit assigned a `needs_feed` job from the
    shared job board would walk straight toward the animal with an
    empty inventory — FEED requires wheat in the *acting unit's own*
    inventory, and nothing routed the unit via the shed first — arrive,
    find nothing to do, then walk all the way back to the shed for
    wheat and back out again. This two-trip pattern couldn't keep pace
    once a herd grew past a handful of animals. Fixed in the main
    job-assignment loop: when the claimed target is a `needs_feed` tile
    and the unit isn't carrying wheat, detour via the shed first. Kept
    regardless of animal scale — it's a correct fix on its own.
  - Also fixed along the way (same as before): `BUY_PRODUCT WHEAT 10`
    needs a `hour == 0` once-per-day gate, or it re-fires every turn
    the shed is under `WHEAT_FEED_BUFFER` ($250/turn, repeatedly).
  - The animal-purchase pacing gate (`safety_margin`) was loosened from
    `cost * (2 + n_owned)` to `cost * (1 + n_owned / 2)` — the original,
    steeper rate was tuned against a much weaker economy (before the
    HIRE fix and strawberry-dominant crop mix existed) and stalled real
    herd growth well below target even with the feed-coordination fix.
  - **Do not casually raise `ANIMAL_PLAN` back toward Seb/HealthStone's
    real scale (8 cow + 12-14 sheep) without also improving hand
    coordination further** — that exact scale was tried this session
    and lost decisively for the reason above (herd instability, not
    economics). A future attempt at a larger herd needs to address *why*
    hands can't keep pace at scale (more hands? smarter per-unit
    feed-routing that batches multiple pasture visits per shed trip?
    something else?) before the scale itself is worth revisiting.
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

Testing each of the three fixes in isolation (methodology below) initially
showed #2 and #3 were strong, real, verified wins, while #1 (animals) was
**not** — it lost badly alone and even combined with the other two fixes,
at the scale Seb's replays showed (8 cow + 12 sheep). **This was revisited
and corrected later the same day** (see "Follow-up: real submission
replay analysis + the animal scale fix" below) — animals at that full
scale genuinely do lose here, but a much smaller scale (4 cows only) wins
decisively once a real feed-coordination bug is fixed. The replay
observation wasn't wrong; the first attempt to act on it was tuned to the
wrong scale for what this agent's hand-coordination could actually
sustain. This is the same lesson as the CROP_WEIGHT regression earlier in
the project, from a related angle: real replay evidence is strong for
*what a winning opponent does*, but the *scale* they run something at
isn't automatically the right scale to copy — it depends on what your own
agent's execution can actually support.

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

### Follow-up, same day: real submission replay analysis + the animal scale fix

After committing/pushing/submitting the HIRE-fix + crop-mix change above
(submission `55362811`), the user separately uploaded a second real-replay
zip: 16 more games, this time capturing **two** independent top-10 players
head-to-head — "Seb (allegedly)" (8/16 in this sample) and "HealthStone"
(7/12) — plus 30 freshly-downloaded real replays of the just-submitted
`55362811` itself (via `kaggle competitions episodes <id> -v` to list, then
`kaggle competitions replay <episode_id>` per game).

**Real result on `55362811`: 11/30 = 36.7% win rate** — worse than the
53.3% seen on the *previous* submission, despite testing at 100% locally.
This agent's own final money is consistently ~$25-30k regardless of
outcome; losses come against opponents averaging ~$51k. Both Seb and
HealthStone average $76-78k in this batch, via a sustained full animal
program (Seb: 8 cow + 14 sheep, 4 quadrants; HealthStone: 9 cow + 5-6
sheep, only **3** quadrants — ruling out "more land" as the explanation)
plus land/tile utilization noticeably higher than this agent's own.

This is what triggered the animal-scale investigation described in the
bullet above. The short version: full real-observed scale (8 cow + 12
sheep) was retried directly and still lost 0/40 even after two real bug
fixes (see the animal-program bullet above for the feed-coordination bug
and the loosened purchase-pacing gate) — the herd couldn't hold steady at
that size. A much smaller scale (4 cows, no sheep) does hold steady and
wins decisively (90%/80% across two 40-seed batches vs. animals-off,
100% vs `main_v1`). **This is now in `main.py`, committed, but not yet
submitted to Kaggle as of this writing** — see Open threads below.

**Building a synthetic strong-opponent test file was itself a real
investigation, not a shortcut** — `main_v1.py` only runs a token 1-of-each
animal setup and was never a valid stand-in for testing "does an
animal-heavy build lose because animals are bad, or because this specific
codebase can't execute them yet." A copy of `main.py` with animals
force-enabled was used instead, and turned out to be genuinely useful for
isolating the feed-coordination bug — worth keeping this pattern in mind
for future strategy questions where `main_v1.py` doesn't represent the
behavior actually being tested against.

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

1. **Submission history**: `55335956` (2026-08-07, hand-scaling +
   CROP_WEIGHT retry) → `55362811` (2026-08-08, HIRE fix + strawberry-
   dominant crop mix, **real result 11/30 = 36.7%**, worse than
   `55335956`'s own real 53.3% despite testing at 100% locally — this is
   what triggered the animal-scale investigation) → **not yet submitted**:
   `main.py` now additionally has the feed-coordination fix and animals
   re-enabled at a small scale (4 cows), verified locally at 90%/80%
   (two 40-seed batches vs. the same agent with animals off) and 100%
   (40/0, avg +$34,072) vs `main_v1` — the strongest local result of the
   project. **Ready for the next submission slot**, pending explicit
   go-ahead.
2. Once submitted and real games accumulate, repeat the same replay
   analysis methodology used twice now (download own real replays via
   `kaggle competitions episodes <id> -v` then `kaggle competitions
   replay <episode_id>` per game; sample day-by-day at `day*24 + 12`;
   compare against a specific strong opponent's replays, not just
   aggregate win rate) — this has surfaced every real fix found so far
   and is clearly worth repeating as a matter of course each time a new
   submission accumulates enough games, not just when stuck.
3. **The animal program's ceiling is now understood, not just "on or
   off"**: this codebase can reliably sustain ~4 animals but not the
   ~20 (8 cow + 12-14 sheep) real top players run, because hand
   coordination can't keep a large herd fed reliably yet (see the
   feed-coordination bug fix above — real, but evidently not sufficient
   on its own at full scale). The natural next lever, if pursuing this
   further, is *why* — more hands specifically dedicated to animal care
   past a certain herd size, a smarter routing rule that batches
   multiple pasture visits per shed trip, or something else entirely.
   Test any such change against the same animals-on-vs-off isolation
   used this session, watching specifically for whether the herd count
   holds steady over time rather than oscillating.
4. **Land/tile utilization gap, not yet investigated**: both Seb and
   HealthStone run meaningfully higher total occupied tiles (crops +
   animal structures combined) than this agent — HealthStone in
   particular reaches ~65-73 occupied tiles on only 3 quadrants (75
   tiles), a higher utilization rate than this agent achieves even on 4.
   Not touched this session; worth its own isolated investigation.
5. Melon's `MELON_TARGET = 15` remains unchanged — both Seb's (13-18)
   and HealthStone's real melon peaks fall inside that range, so no
   evidence has surfaced across two replay batches now to revise it.
