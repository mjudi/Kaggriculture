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
- **Crop diversification** (everything except the melon carve-out): even
  "fewest-planted-count-wins" diversification, not value-weighted. A
  strawberry-weighted version (weight 6:1 over other crops) was tried and
  tested at length — it caused a serious regression (80% win rate vs
  `main_v1` down to 25%) that took real digging to understand. It was
  **not** the ongoing-crop decay mechanic (see below) — that fix made zero
  measurable difference when isolated. It traced to planting strawberry too
  early/aggressively while capital was still tight. Reverting to even
  diversification recovered the loss. The underlying idea (ongoing crops
  are more turn-efficient at scale, since they don't need replanting) may
  still be right; a `CROP_WEIGHT` dict is left defined in the file for a
  future, more carefully isolated attempt — don't just flip it back on
  without a proper A/B test.
- **Animal program: DISABLED.** `choose_animal_program()` returns `None`
  unconditionally as its first line — read the docstring in the file, all
  the logic below that line is dead code kept for a future attempt, not
  currently running. Tested at three separate scales this session (a full
  8-cow/6-sheep plan matching a real top-of-leaderboard replay, and a much
  smaller 2-cow-only version) against an otherwise-identical agent with
  animals off entirely. **Animals lost on every seed tested, at every
  scale.** Two real bugs got found and fixed along the way (a circular gate
  that permanently blocked ever buying anything once one structure sat
  empty with nothing bought for it, and unconstrained building that let
  every idle unit build its own pasture in the same burst — 17+ empty
  pastures existed by day 2 in one test), so this isn't "still broken," the
  mechanism is genuinely implemented and tested correctly and still a net
  negative. Best guess: the reference replay's overall crop economy is more
  efficient than this file's, and animals amplify whatever baseline
  strategy you already have rather than fixing a weaker one — worth
  retrying if the core crop/land/hand economy gets meaningfully stronger,
  not as a change on its own.
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

Two batches of ~24 real games pulled and analyzed (episode replays +
agent-0 logs downloaded via the Kaggle CLI). No crashes/errors in any real
game so far — every loss has been a genuine strategy gap, not a bug.

Pattern found: this agent's own score is fairly consistent regardless of
opponent (roughly $27k-$42k most games). Wins come easily against weak
opponents (some scoring under $6k). Losses come against a real, sizeable
tier of strong opponents scoring $80k-$180k+ — not one outlier, at least
six different named players in that range. The hand-scaling fix above was
a direct response to this: 5 real losses checked all showed hand count
stuck below 7 the whole game, while every strong opponent reached 11-13
hands early. **That fix has not yet had real ladder exposure** — it was
submitted, but no analyzed batch since then is large enough to trust yet
(most of what's been reviewed post-submission overlaps with pre-fix
games). Getting a fresh batch of 20+ games on the current submission and
re-running the same win/loss + hand-count analysis is the natural next
step before changing anything else.

Known strong real opponents studied directly (useful if their replays turn
up again): `lucaskna` / `somewhere after` (confirmed literally identical
code, 692/720 turns byte-for-byte the same — likely a shared/forked public
notebook, not evidence of multi-accounting) run 8 cow + 6 sheep, 12 hands,
3 quadrants, heavy strawberry. `Xmeeeee` runs a faster-ramping hybrid of
melon + modest animals + strawberry. `yang20251228` runs pure melon
monoculture with minimal hands. `Alex` (an earlier, weaker matchup) showed
the original hands+land complementarity finding.

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

1. Get a fresh, large (20+ game) batch of real ladder replays on the
   current (hand-scaling-fix) submission specifically, and re-run the
   win/loss + hand-count-over-time analysis to confirm the fix actually
   moved the needle on the real field, not just against `main_v1` locally.
2. If it did help: the strawberry/ongoing-crop weighting idea is still an
   open, plausible lever (`CROP_WEIGHT` is defined but unused) — worth a
   properly isolated retry, changing only that one thing against a clean
   baseline, learning from exactly how the first attempt went wrong (don't
   let it plant early/aggressively while capital is still tight).
3. The animal program is a real, correctly-built, currently-dead feature —
   only worth revisiting if the core economy gets meaningfully stronger
   first (see reasoning above), and only with a full animals-on-vs-off A/B
   test before trusting it, not a re-enable-and-hope.
