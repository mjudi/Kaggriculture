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

- **Hand scaling**: `target_hands = min(8, 4 + day)` — cap lowered from 12
  to 8 this round, a real bug found while fixing seed-buying throughput
  (see below): HIRE is sorted first before the `maxMarketOrdersPerTurn`
  truncation (see the HIRE-order-starvation fix below), which fixed HIRE
  being starved but created the opposite problem once hands neared 12 --
  12 HIRE orders alone fill the *entire* 10-order turn budget, leaving
  zero room for anything else (including the once-per-day seed-buy batch)
  on that turn. Confirmed directly: the seed-buy batch never fired on any
  real hour-0 turn once hands neared the old cap. 8 still comfortably
  covers real top-10 replay data (observed hand counts ranged 3-14,
  frequently well under 12) while leaving headroom for other hour-0
  purchases.
- **Land purchases**: capped at **3 quadrants, not 4**. A fresh replay
  batch covering 13 distinct top-10 players (not just 1-2, as in earlier
  rounds) showed every single profiled game staying at exactly 3
  quadrants from around day 8 onward, never buying the 4th. Verified in
  isolation: 100% (40/0, avg +$3,261) vs the committed baseline. This
  directly contradicts an earlier finding in this file ("4 quadrants beat
  3") — that finding predates the HIRE-order-starvation fix, the
  seed-buying-throughput fix, and the strawberry-dominant crop mix all
  existing, so it was re-tested with the current stronger baseline rather
  than assumed to still hold, and the new data pointed the other way.
- **Land-timing fix: capital is now reserved toward the next quadrant
  once it's within reach, instead of racing HIRE/seed-buy for the same
  spare cash.** A 61-game win/loss split (see "Fourth round" below)
  found real opponents reach 3 quadrants by **day 8**; this agent
  averaged **day 18-22**. Traced one loss turn-by-turn: single-quadrant
  utilization was already 92-96% by day 6-8 (past the `utilization >
  0.7` gate), but money sat around $200-$320 for days at a time against
  a $1,000 land cost — the gate wasn't the constraint, capital was. The
  actual drain was traced to HIRE's own fibonacci cost (~$54/day for 8
  hires at day 6), not the seed-buy batch first suspected. Fix: a
  `land_pending` flag (true once day/utilization preconditions are
  already satisfied and only cash is missing) that (1) shrinks the
  seed-buy batch's spendable amount by the pending land cost, and (2)
  trims `target_hands` by 2 (not to zero — hands remain this codebase's
  most load-bearing lever, cutting them off entirely risked doing more
  harm than the land delay). Verified in isolation: 75.0% (30/10, avg
  +$903) vs the committed baseline (a seed-buy-only version without the
  HIRE trim tested weaker, 62.5%/+$294, confirming HIRE cost was the
  larger factor).
- **A real, previously undiagnosed bug: seed-buying couldn't keep pace
  with idle land late-game.** `build_market_orders`'s seed-buy block only
  ever purchased 1 seed per turn, gated on stock hitting exactly 0.
  Confirmed directly in a real local run: by day 24-28, 41-53 tiles sat
  empty while wheat seed stock was 0-1 the entire time. Root cause: once
  melon/strawberry age out of `planting_priority` past their maturity
  cutoff (day 20, from the season-end-caution feature), every newly-idle
  tile wants a wheat seed at once, but 1/turn can't keep pace. Fresh
  top-10 replay data shows the fix already reflected in real play: wheat
  tile count climbs to 48-50 by day 26 as strawberry winds down --
  they're backfilling freed land, not leaving it idle. Fixed with a
  once-per-day (`hour == 0`) batch purchase sized to actual demand
  (empty plantable tile count, capped by affordability and a flat max of
  10), checked independently of the "stock == 0" trigger (nesting it
  inside that check was tried first and barely ever fired, since a
  leftover seed from the previous day is usually still in stock right at
  hour 0). The original 1-seed purchase stays as an ungated same-day
  top-up for genuine mid-day stockouts. Verified in isolation, combined
  with the hand-cap fix above (both needed together -- the batch can't
  fire at all without the hand-cap fix freeing up an order slot): 97.5%
  (39/1, avg +$3,321) vs the committed baseline. Note: empty-tile count
  still isn't fully zero even after this fix (hand-coverage of a
  larger occupied footprint may be a secondary, not-yet-addressed
  bottleneck) -- but the net result is still a clear win.
- **A second real bug in the same batch-buy block, found this round:
  `top_crop` was always `eligible[0]`** (main.py's seed-buy batch), and
  `eligible` from `planting_priority` always lists WHEAT first when it's
  eligible -- which is essentially always. So the batch purchase above
  only ever restocked wheat; `CROP_WEIGHT` never had any effect on
  *buying*, only on *planting* once seeds were already in hand.
  Confirmed directly: strawberry seed stock never exceeded 1 the entire
  game in a real local trace, regardless of `CROP_WEIGHT`'s value --
  strawberry was structurally starved at the purchase stage, explaining
  its real ~15-tile ceiling vs strong opponents' 36 far better than the
  diversification weight ever could. See "Fifth round" below for the
  fix (picking `top_crop` by the same under-representation ratio used
  for planting, but excluding WHEAT from that specific comparison) and
  the two real over-correction bugs found getting there.
- **Melon prioritized up to `MELON_TARGET = 11`** ahead of even
  diversification for the rest — lowered from 15 the round before this
  one. A win/loss split across 61 real games found this agent's own
  melon count (~15.7 tiles avg) already exceeded what real strong
  opponents run (~12 peak, from three separately-named players'
  replays), while its own strawberry count (~15 avg) badly lagged
  theirs (36 by day 14). Verified in isolation at the time: 65.0%
  (26/14, avg +$170) vs the committed baseline.
- **A real, previously undiagnosed bug: `MELON_TARGET` was never
  actually enforced as a hard cap** (see "Fifth round" below for the
  full account). It only gated a *priority override* in
  `immediate_action`'s planting block ("plant melon first if under
  target") — once at/over target, melon fell through to the normal
  `CROP_WEIGHT` diversification sort and could still win there and get
  planted, since melon's weight (1) was the same as wheat's. Confirmed
  directly in a real local trace: field count climbed to 12 and 13 in
  turns immediately after already being at the target of 11. Confirmed
  against real replay data too: **every one of 72 real games on the
  submission that shipped `MELON_TARGET = 11` showed melon peaking at
  15-18, never at or below 11** — the fix that was verified and shipped
  never actually took effect the way local testing checked for it (win/
  loss outcomes, not the field-count ceiling directly). This is very
  likely why that submission scored *worse* (546.6) than the one before
  it (559.7). Fixed by excluding melon from the `available` crop-
  selection list entirely once at/over target, not just skipping the
  priority override — confirmed directly: melon now holds at target and
  stays there in a real local trace.
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
  - **This finding held up on a third, independent attempt with much
    stronger evidence.** A fresh replay batch covering 13 distinct
    top-10 players (not just 2, as in the round that produced the
    finding above) showed a smaller real target than previously tried:
    ~8-9 cow + ~4-4.5 sheep, not 12-14 sheep. Tried `ANIMAL_PLAN =
    [("COW", 8), ("SHEEP", 4)]` specifically to check whether the
    earlier failure was really about the *larger* scale (20 animals) or
    about mixing cow and sheep at all. **Still a decisive loss: 0/40,
    avg -$28,200**, with the same mild oscillation symptom as before
    (sheep count fluctuating 4→2→2→3 across sampled days on some
    seeds). This confirms the limitation is not primarily about total
    headcount — a 12-animal mixed herd (8 cow + 4 sheep) failed nearly
    as badly as the earlier 20-animal one. The mixed cow+sheep
    coordination itself (two different pasture types competing for the
    same limited hand attention and feed routing) is the more likely
    root cause. `ANIMAL_PLAN` reverted to `[("COW", 4)]` cow-only,
    unchanged from the previous round's verified-good result. Treat the
    "no sheep without deeper coordination work" finding as fairly
    well-established at this point, not still an open question to keep
    re-testing at slightly different scales.
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
- **Season-end planting caution** (`planting_priority()`, main.py:263):
  a crop no longer becomes eligible to plant once `day +
  CROPS[crop]["first_yield_day"] > SEASON_DAYS` — planting something
  that can't reach even its first possible harvest before turn 720 is
  pure waste (seed cost + a planting action for zero return). Checked
  against `first_yield_day`, not `max_yield_day`, since a late planting
  that still gets one harvest in isn't wasted even if it never reaches
  full yield. In practice this stops MELON/STRAWBERRY plantings after
  day 20 (`30 - 10`); WHEAT is barely affected (cutoff day 28). Verified
  in isolation directly against the committed baseline (not just vs
  `main_v1` — see the note in the section below about why that
  distinction matters): 87.5% (35/5), avg **+$1,864** across a 40-seed
  batch. Modest but real and consistent with real replay data — both
  Seb and HealthStone wind down active strawberry/melon plantings
  starting around day 18-22.
- **Fertilizer: re-tested this round, still disabled.** Same conclusion
  as before, now double-checked against the current (much stronger)
  economy rather than assumed to still hold. See "Optimization round"
  section below for the isolated result.
- **Price momentum / dynamic sell throttling: tried, reverted, not in
  `main.py`.** See "Optimization round" section below.

## Optimization round: assessing external "Grandmaster" suggestions

The user relayed a list of Kaggle-Grandmaster-style suggestions (dynamic
non-deterministic agent, reward-function tuning, opponent-footprint
tracking, queue-aware routing, risk depreciation, etc.) and asked for an
assessment plus a plan. Most of the list doesn't fit this competition or
this agent's actual architecture — worth recording explicitly so a future
session doesn't re-litigate the same rejected ideas without new evidence:

- **Non-determinism** was rejected outright — it conflicts with this
  project's entire testing methodology, which depends on identical seeds
  producing directly comparable A/B results across every round documented
  in this file. Kaggle's own evaluation is also deterministic-episode
  based (`configuration={"seed": N}` reproduces exact games).
- **"Reward function," "state-space design"** are RL/training vocabulary
  that don't map onto what `main.py` is — a fixed rule-priority heuristic
  submitted directly to Kaggle's matchmaking, not a trained policy. There
  is no reward signal or training loop to tune. Reframed as "make good
  local decisions, verify them empirically the same way as everything
  else in this file," not adopted literally.
- **"Opponent footprint... predict clone dumps," "preempt clones when
  public matching states occur"** assume visibility into the opponent's
  shed/strategy that doesn't exist — confirmed directly in README.md:
  "Players are unable to see the state of the other's shed." Not
  implementable as stated; not attempted.
- **"Queue-aware seasonal routing," "delivery time delays"** don't map to
  any real Kaggriculture mechanic (checked against README.md's full
  action list) — travel time is already implicit in the existing
  job-board's nearest-target routing (`agent()`'s main loop, `nearest()`
  helper). Not a new feature to build; already effectively present.

Three pieces of the list WERE genuinely new, testable, and grounded in
real mechanics, and got a real isolated test each:

1. **Price momentum ("trend velocity")** — added `_PRICE_HISTORY`, a
   deliberate module-level exception to this file's normal stateless
   convention (documented at the top of `main.py`), plus
   `record_price_history()` and `price_momentum()`. **Verified
   empirically this round that module-level state DOES persist
   call-to-call within one episode** under local `kaggle_environments`
   (a counter incremented correctly turn-to-turn across a full 720-step
   run) — this wasn't previously confirmed and is worth knowing for any
   future stateful feature. Real Kaggle's actual submission runtime
   isn't confirmed to behave identically, so `price_momentum()` always
   has a safe neutral fallback (returns 0.0, i.e. no adjustment,
   whenever there's insufficient history) rather than assuming data
   exists. Used to scale `SELL_CAP` up/down by a multiplier based on
   recent price trend. **Result: regressed at both multiplier strengths
   tested** — `1 + 0.5*momentum`: 27.5% (11/29), avg -$436; retuned
   gentler to `1 + 0.15*momentum`: still 32.5% (13/27), avg -$315, both
   vs the committed baseline. Not a tuning-magnitude problem (gentler
   barely moved the needle) — more likely this signal fights against
   `SELL_CAP`'s already-empirically-tuned per-item values rather than
   complementing them. **Reverted, not in `main.py`.**
2. **Fertilizer re-test** — `build_market_orders`'s fertilizer purchase
   was hard-disabled via `if False and ...` based on a test from before
   the HIRE-order-starvation fix, the strawberry-dominant crop mix, and
   the small-scale animal program all existed — i.e. against a
   meaningfully weaker economy. Re-enabled unmodified (same
   FERTILIZE/PICKUP logic, same $500/$30-cap gate) and re-tested
   directly against the current baseline. **Result: still a clear net
   negative, 0/40, avg -$21,760.** The old conclusion holds even at the
   stronger economy — this isn't a "the world changed, re-check"
   situation the way animals turned out to be; fertilizer's travel-time
   and per-fertilize-action cost genuinely doesn't recoup here. **Left
   disabled, `if False and` restored.**
3. **Season-end planting caution** — see the bullet in "Current agent
   design" above. **Kept, verified, in `main.py`.**

**A methodology note worth repeating for any future isolated test**:
early results in this round were compared against `main_v1.py` and
looked uniformly excellent (100% win rate for fertilizer, season-end,
AND momentum all individually) — but that's misleading when the
*current baseline itself* already beats `main_v1.py` by a wide, growing
margin. The only test that actually isolates a single change's own
contribution is **against the current committed baseline**, not against
`main_v1.py`. Fertilizer's real result (a severe loss) only showed up
once compared the right way — comparing only against `main_v1.py` would
have led to shipping a net-negative change.

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

### Third round: 13-player replay batch, seed-buying bug, land cap

The user uploaded a third pair of replay zips: 35 fresh real games of the
current submission (`55390463` — **16/35 = 45.7% win rate**) and 10 fresh
top-10 games covering **13 distinct named players** (Abracadabra, THUNDER
THUNDER, Valmorlee, BHackers, TIM, Freddy, Lev Neganov, Jince, Erfan
Eshratifar, Dmitry Larko, Victor @ Tufa Labs, Ak, Hak, Ueddy) — a much
broader sample than either prior round (which covered 1 and 2 players
respectively).

Their crop/pasture counts were remarkably consistent across many
different players (e.g. day 22: 30-33 wheat / 23-28 strawberry / 12-14
pasture, repeated across 6+ different player pairs) — strong evidence
most of the current top tier is running a shared public-notebook-derived
strategy, not independently convergent builds. Same recurring pattern as
every prior round: this agent's own score is stable (~$38-50k) regardless
of win/loss; losses come specifically against opponents averaging
~$77,004, matching this batch's own top-10 average of $75,712 almost
exactly.

Three gaps found, two real and fixed (see "Current agent design" above
for the verified numbers), one tested and rejected:

1. **Seed-buying throughput bug** (real, fixed) — 41-53 empty tiles
   sitting idle late-game while seed stock sat at 0-1. This took two
   attempts to actually fix: the first version's batch purchase was
   nested inside the existing "stock == 0" check and almost never fired
   in practice (a leftover seed is usually still in stock exactly at
   hour 0), and separately, once it did start firing, an initial version
   without a same-day gate drained a real test game's bank from $2,893
   to $34 in three days (same shape as the earlier `BUY_PRODUCT WHEAT`
   bug). Fixing both, plus discovering and fixing the HIRE-cap-crowds-
   everything-else problem (see the hand-scaling bullet above), got a
   clean, verified 97.5% (39/1, avg +$3,321) result.
2. **Land capped at 3 quadrants** (real, fixed) — see "Current agent
   design" above. 100% (40/0, avg +$3,261) in isolation.
3. **Sheep re-attempted at a smaller scale, still rejected** — see the
   animal-program bullet above for the full account. 0/40, avg -$28,200,
   confirming the mixed-herd coordination limitation is real and not
   just a matter of finding the right headcount.
   - **The user pushed back on this rejection directly, and the
     pushback was right to make**: testing sheep only against a
     non-animal baseline (`main_baseline.py`/`main_v1`) doesn't answer
     "does it lose against the *real* opponents it needs to beat" —
     those opponents run animals too. Built a synthetic opponent
     (`opponent_topfield.py`, not kept in the repo) using the current
     strong `main.py` as its base with `ANIMAL_PLAN` forced to the real
     top-10 target (8 cow + 4 sheep) — same coordination code, correct
     target, used purely as a test bar. Result held up, more decisively
     than the baseline test suggested: **cow-only `main.py` beat this
     animal-heavy opponent 100% (30/0, avg +$22,537)**, while **the
     sheep variant only won 30% (9/21, avg -$3,201)** against the exact
     same opponent. This is stronger evidence than the baseline
     comparison, not weaker — it shows cow-only doesn't just clear a low
     bar, it dominates an opponent actually running the real top-10
     animal strategy, and sheep still actively hurts even in that
     specific matchup. The real gap to the top tier isn't "missing
     sheep" — it's that this codebase's mixed-herd coordination
     specifically can't execute what top players' code apparently can,
     while the other changes this round (seed-buy fix, land cap, crop
     mix) are already enough to beat that exact strategy once animals
     are controlled for. **Worth remembering as a general lesson**: when
     a real-replay finding says "top players do X," testing "does X help
     us" against a baseline that doesn't do X is a different, weaker
     question than testing against an opponent that does — build the
     stronger test when the stakes justify it, as was done here.

Combined (seed-buy fix + land cap; sheep excluded): 97.5% (39/1, avg
+$3,321) vs the committed baseline, **100% (40/0, avg +$38,549) vs
`main_v1`** — the new strongest verified result of the project.

### Fourth round: win/loss split on 61 real games, crop rebalance, land timing

The user uploaded a single large batch this round (61 real games of the
current submission `55432490`, no separate top-10 zip) and asked
specifically for wins and losses to be compared directly, not just
aggregated — a genuinely useful framing that hadn't been done as
explicitly in prior rounds.

**Real result: 27W/34L = 44.3%**, consistent with the prior round's
45.7% (a stable number across two large batches, not noise). Comparing
wins and losses directly: **this agent's own play is nearly identical
in both** (same crop-mix shape, same land timing, same 4-cow animal
count, day-by-day money within a few thousand dollars either way) — the
determining factor is who's on the other side, not what we do
differently game to game. Wins average opponent final money of
$30,987; losses average $66,628 — matches every prior round's finding
that the gap to the top tier is structural, not variance.

Checked three of the worst losses in detail (`Adarsh`,
`Boiled-Sweet-Potato`, `Yubo WANG`) — **near-identical crop and animal
counts to each other**, the same shared-template signature seen in
every prior round, running 8 cow + 4 sheep. This is the *exact* scale
the previous round's synthetic-opponent test showed cow-only beating
100% (30/0) — but here, real opponents at that same scale are winning
by 2.5-4.5x. **This is a real, important tension, not yet fully
resolved**: the synthetic opponent shared our own coordination code
with the animal target forced to match, so it necessarily inherited
whatever executes that target *as our codebase would*. These real
opponents are executing something that wins decisively at the same
target — meaning either their code coordinates a mixed herd
meaningfully better than ours can, or (more likely, per the analysis
below) the animal difference isn't actually the deciding factor in
these particular losses and something else in their build is. Two
other gaps were found and are more directly actionable:

1. **Strawberry under-investment relative to melon.** This agent's own
   strawberry tile count peaked at ~15 average (max 20) across all 61
   games; the real opponents reach 36 by day 14. Meanwhile this agent's
   own melon count (~15.7 avg) was already *higher* than what the real
   opponents run (~12 peak). See the `MELON_TARGET` bullet above for
   the fix and verified result (65.0%, avg +$170 in isolation).
2. **Land timing.** Real opponents reach 3 quadrants by day 8; this
   agent averaged day 18-22. Traced turn-by-turn and found the
   `utilization > 0.7` gate was already satisfied by day 6-8 in a real
   loss — capital was the actual constraint (HIRE's fibonacci cost, not
   the newer seed-buy batch, which was the first suspect but turned out
   not to be the larger factor once traced directly). See the
   "land-timing fix" bullet above for the mechanism and verified result
   (75.0%, avg +$903 in isolation).

Combined (crop rebalance + land-timing fix): **87.5% (35/5, avg
+$1,680)** vs the committed baseline — each individually more modest
(65.0%/$170 and 75.0%/$903) but compounding well together. **100%
(40/0, avg +$37,574) vs `main_v1`.**

**Open tension carried forward**: even with both fixes, this doesn't
directly address why real 8cow+4sheep opponents are still beating a
synthetic version of the same build. The land-timing and crop-mix gaps
found this round may explain a meaningful part of that gap on their
own (a stronger baseline economy competing against the same animal
strategy), but this hasn't been re-verified against a fresh synthetic
opponent since these fixes landed — worth doing before assuming the
tension is resolved. See Open threads below.

### Fifth round: the MELON_TARGET fix never actually worked, and why
    `main_baseline.py` gave misleading signal on the real fix

The user was frustrated: public score had been stuck below 600 across
six submissions with no clear upward trend, and asked for an honest
assessment, not another marginal tweak. This round found the two
biggest, most consequential bugs of the whole project.

**155 real games analyzed, episode IDs spanning the full session
history; 72 of them fall within the current submission's actual
episode range** (confirmed via `kaggle competitions episodes
55534146`). Real win rate: 48.6% on the current submission, 44.3-46.5%
on older ones — a stable ceiling, not improving meaningfully submission
to submission. Same recurring pattern as every round: this agent's own
play is nearly identical in wins and losses; losses average opponent
final money of $72,117, 2.6x this agent's own.

**Bug #1 — `MELON_TARGET` was never actually a hard cap.** See the
bullet in "Current agent design" above for the full mechanism. The
short version: the cap only gated a priority *override*, never actually
excluded melon from the fallback diversification pool once at/over
target. **Every one of 72 real games on the submission that shipped the
"lowered MELON_TARGET" fix showed melon peaking at 15-18, never at or
below the supposed cap of 11.** This is very likely why that submission
scored *worse* (546.6) than the one before it (559.7) — a fix that was
verified and shipped never took effect the way it was tested (win/loss
outcomes, not a direct field-count check). Fixed properly this round.

**Bug #2 — the seed-buy batch never actually served strawberry.** See
the bullet in "Current agent design" above. `top_crop` was always
`eligible[0]` (wheat), so `CROP_WEIGHT` had zero effect on purchasing,
only on planting once seeds were already in hand. Fixing this took
three attempts, each one a real lesson:

1. First attempt: picked `top_crop` by the same
   `field_count/CROP_WEIGHT` ratio `immediate_action` uses for
   planting. Result: WHEAT won that comparison at almost every single
   hour-0 check, confirmed via a debug trace (`top_crop=WHEAT` on every
   sampled turn from day 14-18) — WHEAT is a fast one-time crop, often
   caught with a near-zero *field count* right at the hour-0 snapshot
   (harvested, not yet replanted), so its ratio looked "most
   under-represented" almost every time even though its real long-run
   share was fine. Fixed by excluding WHEAT from the batch-buy
   comparison entirely (its own 1-seed same-day top-up is adequate on
   its own, since it's cheap and fast-cycling).
2. Second attempt: sized the batch purchase to the full empty-tile
   count. Result: severe regression, 2.5% (1/39, avg -$2,464) vs the
   committed baseline — strawberry seed stock reached 17 while field
   count only grew to 18 over the same stretch, $1,700+ in cash sitting
   idle as unplanted seed because hand coverage can't plant that fast
   in one day. Buying more than can be planted is real capital waste,
   worse than under-buying. Fixed by capping the batch to a realistic
   one-day planting capacity (`1 + hand_count`) instead of the total
   empty-tile backlog.
3. Third attempt (final): capacity-capped batch. Seed stock now stays
   lean (0-1, no idle capital) and strawberry reaches ~19 tiles in a
   real trace, up from ~12-14 before — still tested as a regression
   against `main_baseline.py` specifically (25.0%, avg -$719).

**The `main_baseline.py` regression was the wrong signal, confirmed by
building a synthetic strong opponent.** Every fix this round that
pushed more strawberry lost against `main_baseline.py`/`main_v1.py`
specifically — strong evidence those weak, non-adaptive opponents can
still win via melon's raw coins-per-action efficiency even when melon
is bugged/oversized, in a way the real competitive tier apparently
can't. Built a synthetic opponent (`main.py`-based, `ANIMAL_PLAN`
forced to 8 cow + 4 sheep matching real replay data — not kept in the
repo) and re-tested there instead: **both the melon-cap fix alone and
the full combined fix beat it 100% across two independent 30-seed
batches (60/60 total), avg margin ~+$23,000.** This is the same
methodology and the same lesson as the third round's sheep re-test:
when a fix looks bad against `main_baseline.py` but the underlying
diagnosis is solid, don't trust the weak-baseline result on its own —
build the stronger test.

**Combined result: 100% (40/0, avg +$33,929) vs `main_v1`, 100% (60/60,
avg ~+$23,000) vs a synthetic 8cow+4sheep opponent modeled on real
replay data.** This is the first round where the core fix was
validated against something resembling the actual competitive tier,
not just a fixed weak baseline — worth treating as the standard going
forward, not a one-off.

### Sixth round: the regression confirmed, and the PIVOT to an animal-dominant build

The fifth-round submission (`55731391`, the MELON_TARGET-enforcement +
strawberry-seed fixes) came back at **public score 515.6 -- WORSE** than
two submissions prior (`55432490` at 567.4). A 110-game replay batch
confirmed the plateau (42.7% real win rate) and, critically, local
head-to-head showed the OLD best version (`main_best567.py`, from commit
bff59e5) beats the current committed `main.py` 65% (13/7). **The last two
submissions genuinely regressed the live ladder position.** The
"MELON_TARGET enforcement fix" specifically hurt -- the best-scoring
version ran melon UNcapped at 15-18, which was accidentally better than
this agent's anemic strawberry.

**The big discovery this round: there is a whole class of winners who win
with a DENSE ANIMAL FARM and almost no crops.** Sorting winners by animal
count found e.g. `cg` ($109,957) running **2 cow + 12 sheep + 7 goose = 21
animals on just ONE quadrant**, ~3 strawberry, melon early then wound
down. Income: wool 285, eggs 249, **fertilizer 432** (sold!), milk 71.
They BUY wheat in bulk to feed (445 units) rather than growing it. This
build sidesteps every problem that sank the crop-heavy rebuild attempts:
no land expansion, no strawberry-seed-throughput problem, no capital split
across many crops.

Extensive effort went into a crop-heavy "match the 36-strawberry winners"
rebuild first -- it did NOT converge. Every fix (homegrown wheat feed,
bulk animal/seed buying, target-based crop allocation, aggressive land)
traded one failure for another because strawberry + animals + wheat + land
all compete for the same ~$3000 early capital. Best crop-rebuild result
~$37k vs the baseline's ~$60k. Documented honestly rather than shipped.

**The animal-dominant build (now in `main.py`) is modeled directly on
`cg`:** `ANIMAL_PLAN = [("SHEEP", 12), ("GOOSE", 7), ("COW", 2)]`, land
expansion disabled (1 quadrant), fertilizer sold, wheat BOUGHT in bulk
sized to herd (not grown), melon early for startup cash. Two real feeding
bugs fixed getting it stable: (1) the once-per-day wheat buy starved a big
herd (it ate through before the next morning) -- now tops up any turn when
low; (2) over-buying wheat then selling the surplus back (buy price > sell
price) was a money leak (833 bought vs 311 sold back) -- now buys only ~2
days of feed and never sells wheat while animals exist. Result: herd
sustains 12 sheep + 5 goose, money climbs to ~$41k (best rebuild all
session).

**Submitted as `55882363` (2026-08-30) despite losing 0/20 LOCALLY to the best crop baseline** -- a
deliberate, evidence-based bet that local testing (which has misled all
session -- see the fifth-round `main_baseline.py` lesson) doesn't capture
that an animal-dominant build beats crop builds on the REAL ladder, as the
real $109k `cg` replay proves. Safety-checked thoroughly first (self-play
DONE, 5/5 vs all built-ins, no crashes across seeds). **This is a genuine
strategy bet, not a validated improvement -- the real ladder score is the
only true test.** If it scores well, the animal-dominant direction is
confirmed and worth pushing toward the full 21-animal / $109k target
(goose+cow still fall short of target -- coop building lags). If it scores
poorly, revert to `main_best567.py` (the real best at 567.4) and treat the
animal-dominant experiment as disproven.

**RESULT (2026-08-30): the bet FAILED decisively. `55882363` scored 369.4
public** -- the worst score of any submission this whole competition, ~198
points below the 567.4 best and well below even the two "regressed" crop
versions (515.3, 507.1). The single-quadrant animal-dominant build does NOT
generalize to real ladder opponents, despite matching one real $109k `cg`
replay. **The animal-dominant direction is now DISPROVEN on the real ladder
-- do not revisit it without fundamentally new evidence.** Per the plan,
`main.py` was reverted to `main_best567.py` (the 567.4 agent) and
re-submitted. Broader lesson reinforced for the Nth time this session: a
single spectacular replay is not a strategy -- it may be a favorable
matchup or variance, not a reproducible edge. The crop-dominant template
(strawberry + melon + modest cow herd + 3 quadrants, `main_best567.py`)
remains the only thing that has ever scored well on the real ladder.

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
   what triggered the animal-scale investigation, public score 494.6) →
   `55386610` (2026-08-09), feed-coordination fix + small-scale animals
   (4 cows), verified locally at 90%/80% vs animals-off and 100% (40/0,
   avg +$34,072) vs `main_v1` (**public score 591.4** once it finished —
   a real, meaningful jump from 494.6) → `55390463` (2026-08-09),
   season-end planting caution added, verified at 87.5% (35/5, avg
   +$1,864) directly against the `55386610` baseline and 100% (40/0, avg
   +$36,113) vs `main_v1` (**real ladder result 16/35 = 45.7%**, public
   score 558.7 — an improvement over `55362811`'s 494.6, though not as
   large a jump as the animal fix produced; this is what motivated the
   third replay-analysis round) → `55432490` (2026-08-11),
   seed-buying-throughput fix + hand-cap-to-8 fix + land cap at 3
   quadrants (see "Current agent design" and the "Third round"
   subsection above), verified at 97.5% (39/1, avg +$3,321) directly
   against the `55390463` baseline and 100% (40/0, avg +$38,549) vs
   `main_v1` (**real ladder result 27/61 = 44.3%**, public score 556.1
   — roughly flat vs `55390463`'s 558.7, and this is what motivated the
   fourth replay-analysis round) → `55534146` (2026-08-15),
   `MELON_TARGET` lowered to 11 + the land-timing fix, verified at 87.5%
   (35/5, avg +$1,680) directly against the `55432490` baseline and
   100% (40/0, avg +$37,574) vs `main_v1` (**real ladder result 72/155
   in-range games = 48.6%, public score 546.6 — actually *worse* than
   `55432490`'s 559.7, despite testing as an improvement; this is what
   triggered the fifth round and the discovery that MELON_TARGET was
   never actually enforced**) → **`55731391` (2026-08-23)**, the real
   `MELON_TARGET` enforcement fix and the seed-buy-batch fix that
   actually lets strawberry compete for bulk purchases (see "Current
   agent design" and the "Fifth round" subsection above), verified at
   100% (60/60, avg ~+$23,000) against a synthetic 8cow+4sheep opponent
   across two independent batches and **100% (40/0, avg +$33,929) vs
   `main_v1`**. Status `PENDING` at submit time. **No real ladder data
   on it yet.** This round is the first where the core fix
   was validated against a synthetic strong opponent instead of relying
   on `main_baseline.py`/`main_v1` alone — every fix that pushed more
   strawberry this round actually *regressed* against those weak
   baselines specifically, which would have looked like a reason to
   revert a fix that was actually correct. The open tension from the
   prior round (real 8cow+4sheep opponents beating this agent decisively
   despite a synthetic version losing 100%/30-0 to cow-only) may now be
   substantially explained by these two bugs rather than an animal-
   coordination gap — worth re-checking once real ladder data comes in
   on this submission.
2. Once submitted and real games accumulate, repeat the same replay
   analysis methodology used four times now (download own real replays
   via `kaggle competitions episodes <id> -v` then `kaggle competitions
   replay <episode_id>` per game; sample day-by-day at `day*24 + 12`;
   compare against a broad top-10 sample, not just 1-2 players if
   available — the 13-player batch gave much cleaner signal than earlier
   1-2-player batches) — this has surfaced every real fix found so far
   and is clearly worth repeating as a matter of course each time a new
   submission accumulates enough games, not just when stuck.
3. **New verification rule, hard-earned this round**: for any future
   cap/target constant (like `MELON_TARGET`), verify it directly against
   the actual field state in a real local trace, not just win/loss
   outcomes. `MELON_TARGET` was silently unenforced for at least two
   full submission rounds because every isolated test checked whether
   the change won or lost, never whether melon count actually stayed at
   or below the number the constant claimed to enforce. A one-line check
   ("does this field count exceed the constant meant to cap it, in a
   real trace, across several sampled days") would have caught this
   immediately. Apply this to `MELON_TARGET`'s current value (11) and
   any similar constant introduced in the future.
4. **New verification rule, also hard-earned this round**: when a fix
   addressing a real, well-diagnosed problem tests as a *regression*
   against `main_baseline.py`/`main_v1.py` specifically, don't
   immediately trust that as the final word — those are weak,
   non-adaptive opponents that can sometimes still win via a strategy
   real strong opponents can't get away with (this round: melon's raw
   coins-per-action efficiency winning locally even while structurally
   bugged/oversized). Build or reuse a synthetic strong opponent
   (`main.py`-based, `ANIMAL_PLAN`/`CROP_WEIGHT` forced to match real
   replay data — not kept in the repo, rebuild as needed) and check
   there before reverting a fix that's otherwise well-evidenced. This is
   now the second time this exact pattern has appeared (the third
   round's sheep re-test, this round's strawberry fixes) — treat it as
   the standard next step whenever a diagnosed-correct fix looks bad
   against the weak baselines, not a one-off escalation.
5. **The synthetic-opponent tension from the third/fourth rounds is
   likely (not yet 100% confirmed) explained by this round's two bugs,
   not an animal-coordination gap.** The third round's synthetic
   8cow+4sheep opponent lost 100%/30-0 to cow-only; the fourth round's
   real replays showed named opponents at that same scale beating this
   agent decisively. This round's synthetic-opponent re-test (same
   8cow+4sheep animal target, but against the current `main.py` with
   both the `MELON_TARGET` enforcement fix and the seed-buy fix applied)
   came back 100% (60/60) in this agent's favor — a much stronger result
   than the third round's already-favorable 100%/30-0. This is
   consistent with explanation (b) from the earlier open item: the
   melon/strawberry bugs were large enough to explain most of the real
   losses, not a genuine animal-coordination deficit. Not fully
   confirmed until real ladder data comes in on the next submission —
   if the real win rate jumps meaningfully once these fixes are live,
   that's strong confirmation; if it doesn't, the animal-coordination
   question is still open and worth revisiting on its own.
6. **Land/tile utilization gap: partially addressed at three different
   layers now, not fully closed.** The third round's seed-buying fix
   reduced but didn't eliminate idle-tile buildup late-game (30-37 empty
   tiles by day 26-28, down from 41-53). The fourth round's land-timing
   fix addresses a different, earlier-game symptom (slow 2nd/3rd
   quadrant purchases) via capital reservation. This round's seed-buy
   fix addresses yet another angle (strawberry seed supply) but
   confirmed a *new* bottleneck one layer down: hand-coverage/planting
   throughput (capping the batch to `1 + hand_count` was necessary
   specifically because more hands can't physically plant faster than
   that in one day). The natural next lever, if pursued: more hands
   reserved for planting specifically, or smarter tile-claiming that
   prioritizes long-idle tiles over nearest-tile-first.
7. Melon's `MELON_TARGET` (11) is now actually enforced for the first
   time — re-verify against future replay batches whether 11 is close
   to right now that it's a real ceiling, since the value was originally
   tuned against a cap that didn't hold.
8. **Price momentum is a plausible idea that failed at the throttling
   layer specifically, not necessarily overall** — it regressed when
   used to adjust `SELL_CAP`, but the underlying signal
   (`price_momentum()`, still in the codebase's git history even though
   reverted from `main.py`) might be more useful applied somewhere else
   entirely, e.g. informing *which* crop to prioritize planting/selling
   rather than *how much* to throttle an already-well-tuned cap. Not
   worth re-attempting without a genuinely different application, not
   just a different multiplier (both tested multipliers this round
   landed in the same regressed range).
9. **Module-level state confirmed to persist within an episode** (see
   "Optimization round" section) — this opens the door to other stateful
   features beyond price momentum (e.g. tracking the agent's own
   historical hand-idle-time, or a running count of harvests per crop)
   if a future idea needs turn-to-turn memory. Real Kaggle's submission
   runtime specifically is still unconfirmed either way — any future
   stateful feature should keep the same defensive-fallback discipline
   used for `price_momentum()`.
