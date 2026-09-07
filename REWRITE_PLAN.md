# Routing rewrite plan: break the labor ceiling via zone ownership

## LAYER A RESULT (2026-09-07): DISPROVEN — zones did not help, do not pursue

Layer A (zone ownership) was built (`main_rewrite.py`, `assign_zones`) and
measured 8-seed vs best567 at both herd sizes. **It made things worse, not
better:**
- herd-15: avg strawberry 9-14 (baseline 11), avg money ~$12k, move% ~44-46,
  still 0/8 vs best567.
- herd-8: strawberry **fell to 17** (was ~25 without zones), money ~$11.3k
  (was ~$12.3k), move% **rose to 50** (was ~43).

**Why it failed (measured, not guessed):** our move% (~43) was ALREADY equal
to keiz's (37-48%) before any zoning, so board-wide movement was never the
bottleneck -- zones can't fix a problem that doesn't exist. Worse, the
zone/fallback machinery added overhead and scattered units (move% went UP).
The REAL bottleneck, seen directly in the spatial diagnostic (seed 6, day
18): **animals monopolize the NW quadrant (11 pastures) leaving no room for
strawberry there, expansion to a 3rd quadrant stalls, and strawberry has
nowhere to grow.** That is a PLACEMENT problem (where pastures go) plus an
EXPANSION-timing problem, NOT a routing problem. Per this plan's own stop
criterion ("if strawberry doesn't rise materially with zones, the ceiling is
structural, not routing -- stop"), Layer A is abandoned.

**Revised hypothesis for any future attempt:** the lever is spatial
PLACEMENT, not routing efficiency -- concentrate pastures into a minimal
footprint (or one quadrant) and reserve whole quadrants for dense strawberry,
the way keiz does (7 pastures NW + 7 NE, but strawberry in ALL three
quadrants: 15+14+21). That is Layer B territory (regional layout), and it
should be tried BEFORE any more routing work. But note: best567 already wins
locally by NOT fighting this at all (small herd, dense melon) -- so the bar
remains "beat best567 locally," and nothing tried so far clears it.

`main_rewrite.py` kept as the record of the Layer-A attempt. `main.py`
remains best567 (unchanged, still the submitted agent).

---

**Status:** proposal for review. No code changes until approved.
**Goal:** make a keiz-style build (15 animals + ~40-50 strawberry across 3
quadrants) executable, so the agent can beat `main_best567.py` locally and
break past the ~510 real-ladder tier.

---

## 1. Why the current agent has a ceiling (root cause, verified)

Confirmed against the engine source (`_apply_unit_action`, line 299):
**each turn a unit either MOVES one tile OR performs one action — never
both.** So the real cost of any job is `Manhattan_distance_to_it + 1`.

The current routing (`agent()`, `main.py:697`) assigns jobs **greedily,
per-unit, board-wide**: each unit independently claims the globally
`nearest()` unclaimed job. With 15 animals across 3 quadrants this thrashes:

- Units cross the whole board — finish a job in NW, get sent to the nearest
  open job in SW (8 tiles away), walk 8 turns for 1 action. Movement
  dominates.
- The **shed is dead-center** and holds all wheat/fertilizer, so every
  feed / fertilizer / animal-delivery job hauls a unit back to the middle.
- No unit **owns a region**, so units oscillate and re-cross paths.

Measured symptom (this session): with the 15-herd, units spent most turns
moving or PASSing; strawberry stalled at ~9 tiles while 25-30 sat empty and
watered crops withered. Direct proof it's labor, not strategy: herd 6 →
strawberry 40 / ~$25k; herd 15 → strawberry 8-19 / ~$10k. `best567` sidesteps
the ceiling entirely by staying small (4 animals, dense 2-quadrant, melon-
heavy) — it never spreads thin.

keiz's real agent runs 15 animals + ~50 strawberry across 3 quadrants on the
same 12 hands, so the ceiling is **routing efficiency**, not the strategy.

---

## 2. Design principle: zone ownership + amortized shed trips

Replace **global greedy** with **spatial partitioning**: each unit gets a
home zone and services only jobs there; shed logistics are batched so one
trip serves many jobs.

```
Current:  12 units ─ compete for ─ ALL jobs board-wide   → long hauls, thrash
Rewrite:  12 units ─ partitioned ─ home zone jobs only    → short hops
```

Geometry that makes this clean (verified): board is 10x10, four 5x5
quadrants, shed at the exact center. Each quadrant has exactly ONE
shed-access tile at its inner corner (`shed_access_tiles`, `main.py:288`),
so every zone touches the shed at one corner — feed/fertilizer pickup is
always reachable from within a zone without leaving it.

---

## 3. The rewrite, in four layers (each independently testable)

### Layer A — Zone assignment (highest impact, do first)

**What:** at the top of each turn, partition unlocked tiles into zones and
assign each unit a home zone. A unit only considers jobs in its own zone;
it falls back to an adjacent zone only when its own has no open job.

**How:**
- Zones = the unlocked quadrants (1-3 of them). When a quadrant holds many
  jobs relative to units, split it in half (e.g. by row) so no zone has far
  more work than one unit can reach.
- Assign units to zones proportional to each zone's **workload**
  (count of pending jobs weighted by urgency), not evenly — an animal-dense
  zone needs feeders, a fresh-planted zone needs waterers.
- **Stable assignment:** a unit keeps its zone across turns unless workload
  shifts materially, so units settle instead of oscillating. Since the agent
  is stateless (no memory between calls), derive a *deterministic* zone for
  each unit from its index + current geometry, so the same unit lands in the
  same zone each turn without stored state.

**New code:** a `assign_zones(units, tiles, farm) -> {unit_name: zone_bounds}`
helper, called once per turn in `agent()` before the unit loop. The per-unit
job `pool` (currently `main.py:819-832`) is filtered to the unit's zone
first, with a board-wide fallback only when the zone is empty.

**Expected effect:** cut cross-board movement 30-50%. This alone may lift
the strawberry ceiling substantially, because units stop abandoning their
quadrant's crops to chase distant jobs.

### Layer B — Regional wheat + batched feed (fixes the animal labor sink)

**What:** eliminate the long central-shed feed haul that makes each animal
expensive in labor.

**How (two complementary changes):**
1. **Grow wheat inside each animal zone.** Plant a few wheat tiles in every
   quadrant that has pastures (keiz does this — wheat appears in all its
   quadrants). A feeder then harvests/pickups feed locally instead of
   trekking to center. Implement by making `planting_priority` /
   the seed-planting logic reserve a small per-zone wheat quota where
   animals live.
2. **Carry-buffer feeding.** A unit assigned to feed does ONE shed pickup
   (PICKUP grabs 5 wheat) then feeds every hungry animal in its zone before
   returning — amortizing the trip. Requires the feed logic to keep feeding
   from carried inventory while any zone animal is hungry, rather than
   one-animal-then-return.

**New/changed code:** feed handling in the unit loop (`main.py:791-804` and
`851-856`), plus a per-zone wheat quota in the planting selection.

### Layer C — Batched shed pickups (cheap, compounds with A/B)

**What:** stop making one shed round-trip per item. When a unit must visit
the shed, it fills up on everything its zone needs (wheat + fertilizer) in
one trip, then services the zone.

**How:** when routing a unit to the shed, PICKUP up to capacity of each
needed good in consecutive turns before heading back out; track carried
inventory and keep applying until depleted.

**Changed code:** the fertilizer-fetch and wheat-fetch blocks
(`main.py:791-817`) merged into a single "shed run" that loads multiple goods.

### Layer D — Global assignment + sticky roles (biggest change, only if needed)

**What:** if A-C don't fully break the ceiling, replace per-unit greedy with
a **turn-level assignment** minimizing total movement, plus **sticky roles**
(feeders own animals, growers own crops, haulers own shed logistics) so units
specialize instead of all doing everything.

**How:**
- Assignment: build the full (unit x job) distance matrix for in-zone jobs
  and assign greedily by smallest distance across the whole matrix (a light
  approximation of optimal assignment), instead of first-come per-unit.
- Roles: derive each unit's role deterministically from its index and the
  current animal/crop counts (stateless), e.g. ceil(herd/5) feeders, rest
  growers, 1-2 haulers.

**Changed code:** the core unit loop in `agent()` (`main.py:753-860`)
restructured from sequential per-unit greedy to matrix assignment.

---

## 4. What is kept unchanged (proven, load-bearing)

- The whole **market layer** (`build_market_orders`, `main.py:492`) — selling
  caps, seed buying, land timing, HIRE. Routing is orthogonal to it.
- The **strategy constants** for the target build (`ANIMAL_PLAN`,
  `MELON_TARGET`, strawberry priority) — the rewrite is about *executing* a
  build, not choosing one. We'll reuse the keiz-clone's strategy layer
  (already in `main_keiz.py`) on top of the new routing.
- All the **engine-mechanic helpers** (`needs_water`, `crop_maxed`,
  `ready_to_harvest`, etc.) — these are verified correct.
- The **feed-once-per-day**, **animal-escape-at-2-unfed**, and
  **move-or-act-not-both** mechanics — the rewrite respects all of them.

---

## 5. Build & validation sequence (incremental, measured)

The session's hard lesson: changes interact unpredictably, and a lopsided
local loss to `best567` predicts ladder failure. So:

1. Start from `main_keiz.py` (has the keiz strategy layer already) on a new
   `main_rewrite.py`. Keep `main.py`/`main_best567.py` untouched as fallback.
2. **Layer A (zones)** → measure: movement %, peak strawberry, herd sustain,
   and a **head-to-head vs `best567` across 8 seeds**. Record before/after.
3. Only proceed to **Layer B** if A helped (or was neutral). Re-measure the
   same metrics. Repeat for C, then D only if still short.
4. **Ship bar:** the rewrite must **WIN (not tie) vs `best567` across ≥6/8
   seeds locally** before any submission. We now know a local blowout loss =
   ladder failure, so "trust the ladder over local" is off the table — local
   dominance is the gate.
5. Crash-safety (self-play + pass/random/starter x seeds 1-3, no exceptions)
   before any submission, same as always.
6. Kaggle submission requires separate explicit user confirmation.

**Fallback:** if even Layer A+B can't get the 15-herd build to win locally,
the conclusion is that the ceiling is deeper than routing (e.g. the
move-or-act constraint fundamentally caps how much 12 hands can do), and we
keep `best567`. That would itself be a valuable, filed result.

---

## 6. Effort & risk

- **Layer A:** ~half a day. Self-contained, reversible, highest expected
  payoff. Low risk.
- **Layer B:** ~half a day. Touches feed logic (historically fragile — the
  feed-starvation seize-up); needs careful testing.
- **Layer C:** ~2 hours. Low risk.
- **Layer D:** ~half to full day. Largest restructure; only if needed.

Total: 1-2 focused days if all layers are needed; possibly much less if
Layer A alone breaks the ceiling.

**Primary risk:** the move-or-act-not-both constraint may impose a hard cap
that no routing can beat — i.e. 12 hands genuinely cannot service 15 animals
+ 50 strawberry no matter how efficient, and keiz wins via something we
haven't identified (e.g. it may run MORE effective hands, or accept lower
per-crop yields). Layer A's measurement will show early whether the ceiling
moves at all; if strawberry doesn't rise materially with zones, that's the
signal the ceiling is structural, not routing, and we stop.

---

## 7. Open questions

**RESOLVED (checked against the replays before finalizing this plan):**
- **keiz runs exactly 12 hands — same as ours.** So the ceiling is NOT a
  hand-count issue; it is purely routing. A cheap hand-count bump is off the
  table as an explanation.
- **keiz's productive-action ratio is only 37-48%** — similar to or LOWER
  than ours. So keiz does not win by working units harder or moving less; it
  wins by moving *to the right places* (short hops that actually reach the
  jobs that matter). This is precisely what zone ownership (Layer A)
  targets, and it's strong evidence the rewrite attacks the real lever.

**Still open, to decide during the build:**
- **Zone boundaries:** quadrant-aligned vs finer subdivision — decide
  empirically once Layer A is measurable.
- **How keiz sequences within a zone** (does it finish one crop's full
  water+care+harvest cycle before moving on, vs interleaving?) — worth a
  closer replay trace if Layer A gets close but not all the way.
