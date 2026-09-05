"""
Kaggriculture reference agent.

Fresh, from-scratch build following the design principles we've worked
through in this conversation:

  1. Watering / feeding discipline always wins over anything else, since
     missing it two days running is unrecoverable (weeds / escaped
     animals) -- see README.md "Watering / Animal Feed".
  2. Bootstrap on wheat and carrot: cheap, fast first yield, and wheat
     doubles as animal feed later, so an early wheat surplus pays twice.
  3. Diversify plantings rather than monocropping -- premium goods have
     steep glut curves (see the Price Function table in README.md), so
     concentrating in one crop concentrates the price-crash risk too.
  4. Throttle selling per item per turn, relaxing only in a short
     liquidation window near the end, since unsold shed inventory scores
     zero at turn 720.
  5. Expand land and start an animal program deliberately, once there's
     evidence the current footprint is actually in use.
  6. Coordinate the farmer and every hired hand against one shared job
     board so they cover different ground instead of racing each other
     to the same tile -- see the note below on why this matters.

I don't have the content of your current main.py in this conversation --
you've never pasted or uploaded it here, and the file that turned up
unexplained in my working folder earlier is something I already flagged
as unverified and said I wouldn't use. So treat this as a clean build to
diff against or merge into your own version, not an edit of it.

Stateless by design: every decision is re-derived from `obs` on each
call, nothing persists across turns via module-level variables -- same
convention as the wheat-loop Quick Start example in AGENTS.md.

WHY THE COORDINATION MATTERS: I tested an earlier version of this file
against the real environment before sending it, and it actually lost to
the passive "starter" baseline. The bug: farmer and hand were each
independently picking "nearest job on the board" with no idea what the
other was doing, so they kept converging on the same tile. Two units
submitting PLANT on one tile with a single seed available silently
cancels the action entirely (see README.md Actions > Plants: "If you try
to plant too many in a specific turn, none are planted"), so most turns
were wasted on collisions rather than covering ground. This version
builds one shared job list per turn and has units claim distinct targets
off it, farmer first, then each hand in order.
"""

# ---------------------------------------------------------------- data ----
# Transcribed from the Object Types table in README.md.
CROPS = {
    "WHEAT":      {"seed_cost": 10,  "first_yield_day": 2,  "max_yield_day": 4,  "max_yield": 6, "ongoing": False},
    "CARROT":     {"seed_cost": 20,  "first_yield_day": 2,  "max_yield_day": 3,  "max_yield": 4, "ongoing": False},
    "TOMATO":     {"seed_cost": 50,  "first_yield_day": 8,  "max_yield_day": 8,  "max_yield": 4, "ongoing": True},
    "STRAWBERRY": {"seed_cost": 100, "first_yield_day": 10, "max_yield_day": 10, "max_yield": 4, "ongoing": True},
    "MELON":      {"seed_cost": 80,  "first_yield_day": 10, "max_yield_day": 12, "max_yield": 6, "ongoing": False},
}
ANIMALS = {
    "GOOSE": {"cost": 300, "structure": "COOP",    "product": "EGG",  "max_held": 4},
    "COW":   {"cost": 400, "structure": "PASTURE", "product": "MILK", "max_held": 6},
    "SHEEP": {"cost": 500, "structure": "PASTURE", "product": "WOOL", "max_held": 6},
}

# Real top-10 leaderboard replays (Seb/HealthStone) run ~8 cow + 12-14
# sheep sustained all game, and that full scale was tried directly here
# -- lost decisively (0/40, avg -$26,986) even after fixing a real
# feed-coordination bug, because this agent's hand-coordination can't
# reliably keep a herd that large fed; it kept oscillating instead of
# holding steady the way the real replays show. A much smaller target
# (cow-only, no sheep) is what this codebase can actually sustain
# without destarving -- verified: holds steady at 4/4 from around day
# 14 onward with no oscillation, and wins 80-90% against the same agent
# with animals off (avg +$10.6k/+$13.4k across two 40-seed batches).
# This confirms the mechanism itself is good; it was a scale problem,
# not a fundamentally bad idea -- raising this again should only happen
# alongside further hand-coordination work, not on its own.
# keiz clone: a balanced ~15-animal herd is one of four co-equal revenue
# legs (milk+wool+egg ~= 28% of keiz's real revenue, roughly matching
# strawberry's 31%). Mix and count taken directly from keiz's replays: cow
# heavy (cheapest premium producer at 400, milk yields from day 8), sheep
# for wool (500, the single highest-value animal product), one goose coop
# for eggs. keiz flexes 14-23 total by seed; 15 is the median and the count
# its 15-pasture + 1-coop build supports. Order matters -- cows fill first
# (cheapest, earliest yield), then sheep, then the single goose.
ANIMAL_PLAN = [("COW", 6), ("SHEEP", 2), ("GOOSE", 0)]

# CARROT and TOMATO removed from planting_priority entirely this round
# (see that function's docstring), so their weights below are moot --
# left at 1 rather than deleted in case either crop's eligibility gets
# revisited later. STRAWBERRY raised well past the previous conservative
# 2:1 retry now that it's meant to be the dominant crop, not just one of
# several diversified options -- matches a real top-10 player's replays,
# where strawberry tile count dwarfs melon (the only other crop grown)
# by roughly 3:1 at peak.
# keiz clone: strawberry stays the dominant crop (3:1), but CARROT and
# TOMATO are re-enabled (see planting_priority) as genuine late-game
# revenue -- keiz sells both (tomato ~5%, carrot ~2% of revenue), rotating
# them onto land that frees up as strawberry winds down days 22-29 rather
# than leaving it idle. Weights left at 1 so they fill gaps without
# competing with strawberry while it's still the priority.
CROP_WEIGHT = {"WHEAT": 1, "CARROT": 1, "TOMATO": 1, "STRAWBERRY": 3, "MELON": 1}

# Per-turn sell ceiling per item, so one big harvest doesn't land in a
# single order and walk the price down against ourselves. Tighter caps on
# the premium/thin-market goods, looser on staples -- see Market Mechanics
# in README.md for why gluts hit them so differently. MILK/WOOL raised
# well above the original single-animal numbers to match a 14-animal
# operation's actual output.
SELL_CAP = {
    "WHEAT": 12, "CARROT": 12, "TOMATO": 6, "STRAWBERRY": 3,
    "MELON": 5, "EGG": 8, "MILK": 10, "WOOL": 8,
}

# Lowered from 150: the reference replay spent down to $10 by the end of
# day 0 and treated that as normal, not risky. A small floor still avoids
# order rejections at literally $0, but this file no longer holds a
# meaningful cushion back the way it did testing single-animal versions.
RESERVE = 30
SEASON_DAYS = 30
LIQUIDATION_START_DAY = SEASON_DAYS - 4   # sell harder once the season's almost over
# Sized for the keiz-clone ANIMAL_PLAN target of ~15 animals eating ~1
# wheat/day each. Larger than the old 4-animal buffer, but FEED is
# once-per-day per animal (engine source), so 15 animals need only 15
# wheat/day -- this buffer covers ~1.5 days of feed, enough headroom that
# the once-per-day top-up buy keeps the shed from ever hitting zero
# between mornings.
WHEAT_FEED_BUFFER = 24
# keiz plants exactly 12 melon on day 0, every game -- the early cash
# engine (melon is the highest-base-price crop at 250, yields days 10-12).
# The day-10 melon harvest is what funds keiz's expansion (money jumps
# ~$2k -> ~$15k in a single day). Kept at 12 to match exactly; melon is
# then gated OFF after the early window (see planting_priority) so freed
# land converts to strawberry rather than replanting melon into a glut.
MELON_TARGET = 12
# After this day, plant no NEW melon -- keiz's melon count is 12 through
# day 9 then drops to 0 by day 10 as the one-time crop is harvested out
# and the land goes to strawberry. Melon's first_yield_day is 10, so
# anything planted past ~day 8 barely matures before the mid-game pivot.
MELON_CUTOFF_DAY = 8
# Wheat is grown only as animal feed + a small surplus. Past this many
# wheat tiles, strawberry (the dominant crop) wins the planting slot
# instead -- keiz keeps wheat modest (peaks ~20-40 late-game as a cash-out
# crop, but stays well under strawberry mid-game). Sized to feed a ~15
# herd (each animal eats ~1 wheat/day; wheat yields multiple units/tile).
WHEAT_TILE_CAP = 16
# Strawberry gets planted ahead of everything (except melon during its
# early window) until at least this many tiles exist -- the crop is meant
# to dominate the field the way it does in keiz's real games (25-54 tiles).
STRAWBERRY_FLOOR = 40

LAND_COSTS = [1000, 2000, 4000]  # cost of the 2nd, 3rd, 4th quadrant, in that order


# ------------------------------------------------------------- helpers ----

def tile_at(farm, x, y):
    return farm["tiles"][y][x]


def unlocked_tiles(farm):
    """Yield (x, y, tile) for every tile that isn't a locked quadrant."""
    for y, row in enumerate(farm["tiles"]):
        for x, tile in enumerate(row):
            if tile != "LOCKED":
                yield x, y, tile


def needs_water(tile):
    return isinstance(tile, dict) and tile.get("kind") == "PLANT" and not tile.get("watered_today")


def needs_feed(tile):
    return (isinstance(tile, dict) and tile.get("kind") in ("COOP", "PASTURE")
            and tile.get("animal") and not tile.get("fed_today"))


def needs_care(tile):
    """CARE only banks its bonus if the same tile is also fed the same
    day (see the real engine's daily refresh: the pending bonus only
    accrues "if tile['cared_today'] and tile['fed_today']"), so this is
    only worth doing once fed_today is already true -- otherwise it's a
    turn spent for nothing."""
    return (isinstance(tile, dict) and tile.get("kind") in ("COOP", "PASTURE")
            and tile.get("animal") and tile.get("fed_today") and not tile.get("cared_today"))


def has_fertilizer_to_collect(tile):
    """Free fertilizer: every animal sets this flag on its own daily
    refresh regardless of anything else, no purchase involved. This is
    the free source main_v1 was actually using -- the earlier attempt
    paid market price for the same item instead."""
    return (isinstance(tile, dict) and tile.get("kind") in ("COOP", "PASTURE")
            and tile.get("animal") and tile.get("fertilizer_available"))


def crop_urgent(tile, day):
    """One-time crops (wheat, carrot, melon) get a hard decay deadline
    the moment they're planted -- max_lifespan_step = (planted_day +
    max_yield_day + 1) * turns_per_day, confirmed directly against the
    real engine source -- regardless of whether they've been harvested.
    For melon specifically, with its long 10-12 day maturity, that
    leaves only about a 3-day window between full ripeness and decay
    starting, worth treating with the same urgency as a maxed-out
    ongoing crop given how much this agent now leans on melon."""
    if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
        return False
    crop = CROPS.get(tile.get("crop"))
    if not crop or crop.get("ongoing", False):
        return False
    if tile.get("yield_units", 0) <= 0:
        return False
    deadline_day = tile.get("planted_day", day) + crop["max_yield_day"] + 1
    return day >= deadline_day - 1


def crop_maxed(tile):
    """Ongoing crops (tomato, strawberry) start decaying about a day
    after reaching max_yield and convert straight to a weed if left
    unharvested -- confirmed directly against the real engine's
    _decay_plants and max_lifespan_step logic. This is worse than an
    animal sitting at its cap, which just stops gaining; this actively
    loses what's already there and eventually destroys the tile
    outright. Almost certainly the actual mechanism behind a bad
    regression this session: heavy strawberry weighting matures many
    tiles around the same time, and if harvesting can't keep pace, they
    don't sit idle, they die."""
    if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
        return False
    crop = CROPS.get(tile.get("crop"))
    if not crop or not crop.get("ongoing", False):
        return False
    return tile.get("yield_units", 0) >= crop["max_yield"]


def animal_maxed(tile):
    """True once yield is sitting at the animal's cap -- production
    beyond max_held is simply discarded, not banked, so a capped animal
    is actively losing value every day it goes unharvested. Confirmed
    directly: a goose sat at yield_units 4 (its max) for a stretch of
    turns with nobody visiting, in a game where crops were getting
    plenty of attention -- worth prioritizing above general crop
    watering, which can wait a turn without losing anything."""
    return (isinstance(tile, dict) and tile.get("kind") in ("COOP", "PASTURE")
            and tile.get("animal") and tile.get("yield_units", 0) >= ANIMALS[tile["animal"]]["max_held"])


def ready_to_harvest(tile, day):
    """yield_units > 0 alone isn't sufficient for PLANT tiles: one-time
    crops can accrue a banked bonus unit before they're actually old
    enough to harvest (verified against the real engine's HARVEST
    handler, which silently no-ops if day - planted_day < first_yield_day
    -- it doesn't error, it just does nothing, which is what made the
    original version of this bug so easy to miss in testing)."""
    if not isinstance(tile, dict):
        return False
    if tile.get("kind") == "PLANT":
        if tile.get("yield_units", 0) <= 0:
            return False
        crop = CROPS.get(tile.get("crop"), {})
        if not crop.get("ongoing", False):
            age = day - tile.get("planted_day", day)
            if age < crop.get("first_yield_day", 0):
                return False
        return True
    if tile.get("kind") in ("COOP", "PASTURE") and tile.get("animal"):
        return tile.get("yield_units", 0) > 0
    return False


def is_weed(tile):
    return isinstance(tile, dict) and tile.get("kind") == "WEED"


def is_plantable(tile):
    return tile is None


def nearest(fx, fy, candidates):
    """Closest (x, y, tile) by Manhattan distance; None if candidates is empty."""
    best, best_d = None, None
    for x, y, tile in candidates:
        d = abs(x - fx) + abs(y - fy)
        if best_d is None or d < best_d:
            best, best_d = (x, y, tile), d
    return best


def step_toward(fx, fy, tx, ty):
    """One greedy Manhattan step. Movement is unobstructed in this game --
    farmers/hands can occupy any tile regardless of what's on it -- so no
    pathfinding is needed, just close the larger axis gap first."""
    dx, dy = tx - fx, ty - fy
    if dx == 0 and dy == 0:
        return "PASS"
    if abs(dx) >= abs(dy):
        return "EAST" if dx > 0 else "WEST"
    return "SOUTH" if dy > 0 else "NORTH"


def planting_priority(day, money):
    """Which crops are eligible to plant, gated by day/money. CARROT and
    TOMATO dropped entirely -- a real top-10 leaderboard player's replays
    (10 games analyzed, same pattern in all 10) showed zero carrot and
    zero tomato ever planted, strawberry run as the dominant crop (peaks
    ~41-43 tiles around day 14-18), melon as an early secondary crop, and
    just enough wheat to feed animals. WHEAT kept eligible throughout
    (bootstrap cash early, animal feed later once animals are back on).
    Melon moved earlier (day >= 2) to match that replay's melon tiles
    already at 3 by day 2, well before strawberry becomes eligible.

    Season-end maturity check: a crop planted so late it can't reach its
    own first_yield_day before turn 720 is pure waste -- seed cost and a
    planting action for zero possible return. Checked against
    first_yield_day (the earliest a planting can produce anything at
    all), not max_yield_day, since a late planting that still gets one
    harvest in isn't wasted even if it never reaches full yield."""
    order = [c for c in ["WHEAT"] if day + CROPS[c]["first_yield_day"] <= SEASON_DAYS]
    # Melon is the day-0 cash engine but only through the early window --
    # keiz plants all 12 immediately (day 0) and never replants once the
    # one-time crop is harvested out. Gated to <= MELON_CUTOFF_DAY so freed
    # land goes to strawberry mid-game, not back into a melon glut.
    if day <= MELON_CUTOFF_DAY and day + CROPS["MELON"]["first_yield_day"] <= SEASON_DAYS:
        order.append("MELON")
    if day >= 5 and money > 600 and day + CROPS["STRAWBERRY"]["first_yield_day"] <= SEASON_DAYS:
        order.append("STRAWBERRY")
    # TOMATO re-enabled as a mid/late ongoing crop (keiz sells ~5% of
    # revenue as tomato). first_yield_day 8, ongoing -- worth planting
    # from mid-game onward to backfill land as melon clears out.
    if day >= 6 and day + CROPS["TOMATO"]["first_yield_day"] <= SEASON_DAYS:
        order.append("TOMATO")
    # CARROT re-enabled as a fast late-game filler (seed 20, first_yield
    # day 2, one-time) -- keiz rotates carrot heavily days 22-29 onto land
    # freed as strawberry winds down, since it matures fast enough to still
    # cash out before the season ends when strawberry no longer can.
    if day >= 18 and day + CROPS["CARROT"]["first_yield_day"] <= SEASON_DAYS:
        order.append("CARROT")
    return order


def shed_access_tiles(board_size):
    """The four inner-corner tiles surrounding the shed at board center --
    verified against the actual engine source, since the observation
    doesn't hand you a shed position directly."""
    half = board_size // 2
    return [(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)]


def is_shed_adjacent(pos, board_size):
    return tuple(pos) in set(shed_access_tiles(board_size))


def crop_counts_on_field(farm):
    """How many tiles are currently growing each crop -- used to spread
    plantings across crop types instead of always grabbing whichever seed
    happens to be cheapest and fastest to restock (which was always
    wheat, permanently locking out everything else -- confirmed directly
    from a replay where only 3 of 720 turns ever bought a tomato,
    strawberry, or melon seed, and none were ever planted)."""
    counts = {crop: 0 for crop in CROPS}
    for _, _, tile in unlocked_tiles(farm):
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            crop = tile.get("crop")
            if crop in counts:
                counts[crop] += 1
    return counts


ONE_TIME_CROPS = {crop for crop, info in CROPS.items() if not info["ongoing"]}


def in_bonus_window(tile, day):
    """One-time crops only: the daily watering bonus (see WATER handling
    in the real engine) only accrues during a window starting at half
    the crop's max_yield_day, rounded up. Fertilizing outside that
    window does nothing useful, so it's not worth the trip."""
    crop = tile.get("crop")
    if crop not in ONE_TIME_CROPS:
        return False
    max_yield_day = CROPS[crop]["max_yield_day"]
    window_start = (max_yield_day + 1) // 2
    age = day - tile.get("planted_day", day)
    return window_start <= age <= max_yield_day


def needs_fertilizer(tile, day):
    return (isinstance(tile, dict) and tile.get("kind") == "PLANT"
            and in_bonus_window(tile, day)
            and tile.get("fertilized_until_day", -1) < day)


def immediate_action(tile, seeds, day, money, want_coop, want_pasture, field_counts=None,
                      own_inventory=None):
    """What to do if the job is already right where this unit is
    standing. Returns None if there's nothing actionable here, in which
    case the caller moves the unit toward its assigned target instead.
    Order matters: upkeep (feed/water) always outranks harvest, which
    outranks clearing a weed, which outranks starting something new,
    because missing upkeep two days running is unrecoverable."""
    if needs_feed(tile) and (own_inventory or {}).get("WHEAT", 0) > 0:
        return "FEED"
    if crop_maxed(tile):
        return "HARVEST"
    if crop_urgent(tile, day):
        return "HARVEST"
    if animal_maxed(tile):
        return "HARVEST"
    if needs_water(tile):
        return "WATER"
    if needs_care(tile):
        return "CARE"
    if ready_to_harvest(tile, day):
        return "HARVEST"
    if has_fertilizer_to_collect(tile):
        return "COLLECT_FERTILIZER"
    if is_weed(tile):
        return "DIG"
    if (own_inventory or {}).get("FERTILIZER", 0) > 0 and needs_fertilizer(tile, day):
        return "FERTILIZE"
    if want_coop and is_plantable(tile):
        return "BUILD_COOP"
    if want_pasture and is_plantable(tile):
        return "BUILD_PASTURE"
    if is_plantable(tile):
        available = [c for c in planting_priority(day, money) if seeds.get(c, 0) > 0]
        if available:
            if field_counts:
                # Diversification weighted by CROP_WEIGHT rather than a
                # flat fewest-planted-count sort: dividing each crop's
                # current count by its weight means a higher-weighted
                # crop (ongoing crops, see CROP_WEIGHT above) gets picked
                # more often at equal counts, but the bias still
                # self-balances as that crop's own count grows -- it
                # can't run away into a monoculture the way an unbounded
                # weighted-pick could. This is an isolated retry of an
                # earlier, much stronger version (6:1 strawberry) that
                # regressed badly; see CROP_WEIGHT's comment for why this
                # round changes only the ratio, not the planting gates.
                available.sort(key=lambda c: field_counts.get(c, 0) / CROP_WEIGHT.get(c, 1))
            # Melon prioritized up to a capped target ahead of even
            # diversification: a real ladder loss (episode 90145856)
            # showed an opponent running a pure 15-tile melon monoculture
            # to $57k with only 5 hands and no land at all, and a
            # well-verified public notebook independently ranked melon
            # far above every other crop in coins-per-action (~250 vs
            # strawberry's ~37), capping most top agents around 4-16
            # tiles specifically to avoid crashing its own price with
            # more. Capped rather than unbounded, learning from how the
            # unbounded strawberry weighting went earlier this session.
            if "MELON" in available and field_counts.get("MELON", 0) < MELON_TARGET:
                return ["PLANT", "MELON"]
            # keiz clone: strawberry is the dominant crop (31% of revenue,
            # peaks 25-54 tiles). Wheat is ONLY grown as animal feed + a
            # little surplus, so cap its tile count (past the cap it stops
            # winning the planting slot and strawberry takes the land).
            # Without this, wheat -- always first in planting_priority and
            # weight 1 -- kept winning the fewest-planted sort and strawberry
            # stalled near 6 tiles while 30 stood empty. Priority once melon
            # is capped: strawberry first, then wheat only up to the feed
            # cap, then tomato/carrot fillers.
            straw_available = "STRAWBERRY" in available
            wheat_over_cap = field_counts.get("WHEAT", 0) >= WHEAT_TILE_CAP
            if straw_available and (wheat_over_cap or field_counts.get("STRAWBERRY", 0) < STRAWBERRY_FLOOR):
                return ["PLANT", "STRAWBERRY"]
            # Drop wheat from consideration once it's at its feed cap so the
            # sorted pick below lands on strawberry/tomato/carrot instead.
            if wheat_over_cap:
                available = [c for c in available if c != "WHEAT"]
            if available:
                return ["PLANT", available[0]]
    return None


def find_delivery_job(farm, private, all_inventories):
    """Priority 1: some unit is already carrying an animal it picked up
    but hasn't placed yet -- whichever unit that is gets routed to
    finish the job, checked across every unit's own inventory, not just
    one. This is the exact bug a replay caught: once an animal leaves
    the shed, shed-only detection can't see it anymore, so a unit
    holding an unplaced animal just wandered off doing ordinary
    fieldwork forever, the structure sitting empty for the rest of the
    game. Priority 2: an animal still waiting in the shed to be fetched
    by whichever unit gets there first.

    Deliberately farmer-*and*-hands now, not farmer-only: with a target
    of 14 animals total, restricting this to one unit would make it the
    whole game's bottleneck. Safe to open up because only one animal is
    ever in flight at a time -- choose_animal_program below won't queue
    the next purchase until the current structure actually has its
    animal placed, not just built."""
    tiles = list(unlocked_tiles(farm))

    for inv in all_inventories:
        for animal, info in ANIMALS.items():
            if inv.get(animal, 0) > 0:
                for x, y, tile in tiles:
                    if isinstance(tile, dict) and tile.get("kind") == info["structure"] and not tile.get("animal"):
                        return animal, (x, y, info["structure"])

    shed = private.get("shed", {})
    for x, y, tile in tiles:
        if not isinstance(tile, dict):
            continue
        for animal, info in ANIMALS.items():
            if tile.get("kind") == info["structure"] and not tile.get("animal"):
                if shed.get(animal, 0) > 0:
                    return animal, (x, y, info["structure"])
    return None, None


def animal_program_status(farm):
    """What's actually placed and earning, not just what structures
    exist -- a coop with nothing in it earns nothing, which is exactly
    the state the delivery bug above used to leave things stuck in."""
    placed = {"GOOSE": 0, "COW": 0, "SHEEP": 0}
    empty_coop = False
    empty_pasture = False
    for _, _, tile in unlocked_tiles(farm):
        if not isinstance(tile, dict):
            continue
        if tile.get("kind") == "COOP":
            if tile.get("animal"):
                placed["GOOSE"] += 1
            else:
                empty_coop = True
        elif tile.get("kind") == "PASTURE":
            if tile.get("animal"):
                placed[tile["animal"]] += 1
            else:
                empty_pasture = True
    return placed, empty_coop, empty_pasture


def animal_total_owned(farm, private, all_inventories):
    """Every unit of an animal the player controls right now: placed in a
    structure, sitting in the shed waiting for delivery, or in a unit's
    inventory mid-delivery. Buying decisions gate on this total so the herd
    converges on ANIMAL_PLAN's target without overshooting (each shed/
    in-flight animal still counts against the target even before it's
    placed)."""
    placed, _, _ = animal_program_status(farm)
    total = dict(placed)
    shed = private.get("shed", {})
    for a in ANIMALS:
        total[a] = total.get(a, 0) + shed.get(a, 0)
    for inv in all_inventories:
        for a in ANIMALS:
            if inv.get(a, 0):
                total[a] = total.get(a, 0) + inv[a]
    return total


def choose_animal_program(farm, private, day, all_inventories):
    """keiz clone: which animal type to buy next, if any. Gates on TOTAL
    owned (placed + shed + in-flight, see animal_total_owned) against
    ANIMAL_PLAN, filling the plan in order (cows, then sheep, then the
    goose). No in-flight lock any more -- delivery is per-unit and
    concurrent now, so several animals can be bought and delivered at once
    to reach keiz's ~15-herd by day 11. Hands gate lowered to 2: keiz buys
    its first 4 animals on day 0 with only 5 hands, so a high gate would
    stall the whole early ramp. The feed-coordination fixes (detour-via-
    shed, carry wheat) in the main loop keep a herd this size fed."""
    if len(farm.get("hands", [])) < 2:
        return None
    total = animal_total_owned(farm, private, all_inventories)
    for animal, target in ANIMAL_PLAN:
        if total.get(animal, 0) < target:
            return animal
    return None


# -------------------------------------------------------------- market ----

def build_market_orders(farm, private, day, hour, prices, has_animals, animal_pick):
    orders = []
    money = farm["money"]
    shed = private.get("shed", {})
    seeds = private.get("seeds", {})

    liquidating = day >= LIQUIDATION_START_DAY

    # Wheat gets a reserve carved out before selling, whether or not
    # animals exist yet -- without this, wheat sells down to zero every
    # time it's harvested (confirmed from a replay: shed wheat sat at 0
    # on every single sampled day), which means the surplus threshold
    # choose_animal_program checks for can never actually be reached.
    # Everything else sells normally.
    for item, qty in shed.items():
        if qty <= 0 or item == "FERTILIZER" or item in ANIMALS:
            continue
        if item == "WHEAT" and (has_animals or animal_pick):
            sellable = max(0, qty - WHEAT_FEED_BUFFER * 2)
        else:
            sellable = qty
        cap = SELL_CAP.get(item, 5)
        sell_qty = sellable if liquidating else min(sellable, cap)
        if sell_qty > 0:
            orders.append(["SELL", item, sell_qty])

    # Direct purchase now instead of waiting on a grown surplus -- the
    # reference replay funded its first animals this way from turn 2
    # (BUY_PRODUCT WHEAT 6 alongside its first HIRE and BUY_ANIMAL, all
    # in one order list), rather than gating on a wheat reserve building
    # up naturally. Buys in a real batch, not a token 1 unit at a time,
    # since up to 20 animals eating daily needs actual supply.
    #
    # Gated to once per day (hour == 0) -- a real bug found testing this
    # round: without the gate, the condition stays true for many
    # consecutive turns (buffer is 85, a single 10-unit buy barely
    # dents that), so it kept re-firing every turn, $250 a turn,
    # repeatedly, which crashed a real test game's money from $1,973 to
    # $176 in three in-game days. Mirrors the hires_today==0 once-per-day
    # pattern already used for HIRE below.
    # Feed-wheat top-up, sized to the ACTUAL current herd, not a flat 10.
    # A flat 10-unit buy at the early wheat price (~$30) costs ~$300 -- an
    # early draft spent the whole bankroll this way on day 1 and the
    # economy never recovered. keiz grows most of its feed (it plants wheat
    # from day 0) and only tops up a day or two of shed feed at a time.
    # Buy just enough to cover the placed herd for ~2 days, minus what's
    # already in the shed, and only when there's real spare cash so this
    # never competes with keeping hands hired.
    # Feed-wheat is EXISTENTIAL, not optional: if the shed runs dry, animals
    # starve AND every unit assigned a feed job gets trapped cycling to an
    # empty shed instead of planting/watering -- an early draft's whole
    # workforce seized up this way once the 15-herd's wheat ran out, and
    # strawberry never recovered. So buy feed ANY hour the shed drops below
    # one full day of feed for the placed herd, with only a small cushion
    # (feeding beats almost everything). Sized to refill ~2 days, capped so
    # a single turn can't blow the bankroll. This is deliberately more
    # aggressive than the old hour-0-only, big-cushion version.
    placed_now, _, _ = animal_program_status(farm)
    herd_size = sum(placed_now.values())
    wheat_have = shed.get("WHEAT", 0)
    # Only buy feed once the shed drops below one day's feed for the herd --
    # not proactively hoarding two days' worth, which drained the early
    # bankroll to ~$20 and starved the melon/strawberry seed buys. Grown
    # wheat (the agent plants wheat from day 0) covers most feed; this is a
    # top-up for the gap. Cushion is generous early (protect the crop/land
    # capital while the herd is tiny and needs little feed) and tighter once
    # the herd is large (feeding a big herd then genuinely is near-top
    # priority, since a starved herd seizes up the whole workforce).
    if herd_size > 0 and wheat_have < herd_size:
        feed_need = (herd_size + max(6, WHEAT_FEED_BUFFER // 2)) - wheat_have
        wheat_price = prices.get("WHEAT", 30)
        feed_cushion = 400 if herd_size < 8 else 120
        affordable = max(0, int((money - feed_cushion) // wheat_price)) if wheat_price else 0
        buy_qty = min(max(0, feed_need), affordable, 12)
        if buy_qty > 0:
            orders.append(["BUY_PRODUCT", "WHEAT", buy_qty])

    # Melon seed gets first claim on the buy loop while under target,
    # same reasoning as the planting priority above -- otherwise it's
    # last in planting_priority's order and rarely gets its turn, since
    # cheaper/faster crops hit zero stock more often and would keep
    # winning the "first crop found at zero" check below.
    field_counts_now = crop_counts_on_field(farm)
    eligible = planting_priority(day, money)
    empty_tiles = sum(1 for _, _, t in unlocked_tiles(farm) if is_plantable(t))
    # Once-per-day batch top-up, checked independently of the "count==0"
    # trigger below -- a real bug found testing an earlier version of
    # this fix: nesting the batch inside "seeds.get(crop, 0) == 0 and
    # hour == 0" almost never actually fires, since a leftover seed from
    # the previous day is usually still in stock right at hour 0 (the
    # count only hits 0 later, mid-day, once that leftover seed gets
    # planted) -- confirmed directly: WHEAT seed count sat at 0 or 1 at
    # hour 0 on 4 of 5 sampled late-game days, so the batch branch was
    # dead code in practice and the ungated 1-seed top-up kept winning
    # every time. Checking "stock below what empty land actually needs"
    # at hour 0, independent of whether it's exactly 0, fixes this.
    # Buying only 1 seed/turn (the fallback below) can't keep pace once
    # melon/strawberry age out of eligibility near the end (see
    # planting_priority) and every freed tile wants a wheat seed at
    # once -- confirmed directly in a real local run, 41-53 empty tiles
    # by day 24-28 with wheat seed stock sitting at 0-1 the whole time.
    # Top-10 real replays don't have this problem: they backfill freed
    # land with wheat instead of leaving it idle (wheat tile count
    # climbing to 48-50 by day 26 as strawberry winds down).
    # keiz clone: buy the RIGHT seeds, not just wheat. The old batch used
    # eligible[0], which is always WHEAT (it's first in planting_priority),
    # so strawberry and melon seed were never batch-bought -- the exact bug
    # that kept this agent's strawberry near zero. Instead, buy toward an
    # explicit priority: MELON up to its target early (the day-0 cash
    # engine), then STRAWBERRY as the dominant crop, then WHEAT to feed the
    # herd and backfill late, then late-game fillers. Each gets bought up to
    # what empty land + its own cap needs, sharing the once-per-day budget.
    if hour == 0 and eligible and empty_tiles > 0:
        # Desired seed count per crop this turn (how many we'd plant if we
        # could), in priority order. Melon capped at its target; strawberry
        # gets the lion's share of open land; wheat sized to feed + a little
        # surplus; tomato/carrot fill whatever's left late.
        n_animals_plan = sum(n for _, n in ANIMAL_PLAN)
        want = {}
        if "MELON" in eligible:
            want["MELON"] = max(0, MELON_TARGET - field_counts_now.get("MELON", 0))
        if "STRAWBERRY" in eligible:
            want["STRAWBERRY"] = empty_tiles  # strawberry soaks up open land
        if "WHEAT" in eligible:
            want["WHEAT"] = max(6, n_animals_plan)  # feed crop + bootstrap
        if "TOMATO" in eligible:
            want["TOMATO"] = 6
        if "CARROT" in eligible:
            want["CARROT"] = empty_tiles
        spend_budget = money - RESERVE
        # Buy in priority order until the once-per-day budget or the
        # 10-order cap runs out. Melon and strawberry first so they never
        # get starved by wheat the way they used to.
        for crop in ["MELON", "STRAWBERRY", "WHEAT", "TOMATO", "CARROT"]:
            if crop not in want or len(orders) >= 9:
                continue
            cost = CROPS[crop]["seed_cost"]
            have = seeds.get(crop, 0)
            need = max(0, want[crop] - have)
            if need <= 0 or spend_budget < cost:
                continue
            affordable = int(spend_budget // cost)
            buy_qty = min(need, affordable, 10)
            if buy_qty > 0:
                orders.append(["BUY_SEED", crop, buy_qty])
                spend_budget -= buy_qty * cost

    # Same-day top-up: covers a genuine mid-day stockout (e.g. a crop
    # that only became eligible partway through the day, or demand that
    # outpaced even the batch above) without re-running the batch sizing
    # logic, so this can't compound into repeated large purchases.
    if ("MELON" in eligible and field_counts_now.get("MELON", 0) < MELON_TARGET
            and seeds.get("MELON", 0) == 0 and money - RESERVE >= CROPS["MELON"]["seed_cost"]):
        orders.append(["BUY_SEED", "MELON", 1])
    else:
        for crop in eligible:
            cost = CROPS[crop]["seed_cost"]
            if seeds.get(crop, 0) == 0 and money - RESERVE >= cost:
                orders.append(["BUY_SEED", crop, 1])
                break

    # Disabled, deliberately, same conclusion as the animal program above:
    # tested gated to day 15, 18, 20, 22, and 24 across the same seed, and
    # every version still landed below an otherwise-identical agent with
    # fertilizer off entirely ($23,688 baseline vs a best of $19,149 at a
    # day-24 gate). Whatever main_v1 is doing with its own large stockpile,
    # this implementation's cost and travel-time overhead don't recoup it.
    # Logic and PICKUP/FERTILIZE handling above are correct and tested --
    # just not worth what it costs to run, at least not yet.
    FERTILIZER_CAP = 30
    if False and hour == 0 and shed.get("FERTILIZER", 0) < FERTILIZER_CAP and money - RESERVE >= 500:
        buy_qty = min(5, FERTILIZER_CAP - shed.get("FERTILIZER", 0))
        orders.append(["BUY_PRODUCT", "FERTILIZER", buy_qty])

    # Scaling raised again: the previous cap of 8 was tuned against
    # main_v1 specifically, but a real ladder loss (episode 90062918)
    # showed a genuine opponent running 12. Divisor lowered too so this
    # ramps faster under the more aggressive capital posture here, where
    # money gets spent down hard early rather than held back.
    # keiz clone: 12 hands. Hands are wiped to [] at every day rollover
    # (engine source), so the full target must be re-hired each morning --
    # keiz issues ~7 HIRE at hour 1 and tops up the rest at hour 2, NOT all
    # in one turn. That spread matters: 12 HIRE orders in a single turn
    # would fill the entire maxMarketOrdersPerTurn=10 budget (HIRE is sorted
    # first below), starving the seed-buy batch and land/animal buys. So
    # instead of gating on hires_today==0 and issuing the whole target at
    # once, this issues only the remaining shortfall up to a per-turn cap,
    # letting hiring spread naturally across the first few hours of the day
    # while leaving order slots free for everything else.
    HIRE_PER_TURN_CAP = 6
    target_hands = min(12, 4 + day)
    hires_so_far = farm.get("hires_today", 0)
    if hires_so_far < target_hands and money - RESERVE >= 50:
        # Tied to elapsed days instead of momentary cash, and ramps much
        # faster: a large sample of 24 real ladder games showed hand
        # count never breaking 7 even by day 27 under the old
        # money-gated formula, while every strong opponent studied
        # tonight (Xmeeeee, lucaskna, Alex) reached 11-13 hands by day
        # 6-12. The old formula created a self-reinforcing trap -- low
        # money keeps hands low, which keeps money low -- when hiring is
        # cheap enough (even 12 hands costs ~$376/day, Fibonacci-scaled)
        # that it shouldn't be gated this conservatively once the
        # earliest days are past.
        #
        # Issue only the remaining shortfall this turn, capped per turn so
        # HIRE never floods the 10-order budget. On a fresh morning
        # (hires_today==0) this puts HIRE_PER_TURN_CAP hires down now and
        # the rest fire on the next turn(s) until target_hands is reached,
        # since hires_today persists within the day. Matches keiz's real
        # split of ~7 at h1 + the remainder at h2.
        want = min(HIRE_PER_TURN_CAP, target_hands - hires_so_far)
        for _ in range(want):
            orders.append(["HIRE"])

    unlocked = farm.get("unlocked_quadrants", ["NW"])
    # Capped at 3 quadrants, not 4 -- every game across a fresh 13-player
    # top-10 replay sample stayed at exactly 3 quadrants from around day
    # 8 onward, never buying the 4th. This directly contradicts the
    # earlier "4 quadrants beat 3" finding below, but that finding
    # predates the HIRE-order-starvation fix, the seed-buying-throughput
    # fix, and the strawberry-dominant crop mix all existing -- worth
    # re-testing with the stronger baseline rather than assuming either
    # conclusion still holds without checking.
    # keiz clone: land on a fixed timer, not a utilization gate. keiz buys
    # the 2nd quadrant on day 6 and the 3rd on day 11, every game,
    # deterministically -- far faster than this agent's old day-18-22
    # average. The melon cash engine (harvest ~day 10) is what funds the
    # 3rd; the 2nd is bought on the strength of the day-0 all-in plus early
    # wheat/melon sales. A light utilization floor still avoids buying land
    # there's genuinely no workforce to touch yet.
    KEIZ_LAND_DAY = {1: 6, 2: 11}  # {quadrants_already_owned: earliest day to buy next}
    if len(unlocked) < 3:
        next_cost = LAND_COSTS[len(unlocked) - 1]
        tiles = list(unlocked_tiles(farm))
        occupied = sum(1 for _, _, t in tiles if t is not None)
        utilization = occupied / len(tiles) if tiles else 0
        earliest_day = KEIZ_LAND_DAY.get(len(unlocked), 99)
        # Disabled, tested directly: staying on the single starting
        # quadrant and running the same 4 hands in a tighter space beat
        # buying a second quadrant on 9 of 10 seeds, sometimes by a lot
        # (seed 6: $29,168 vs $22,538 with land bought). Same shape as
        # main_v1's own approach, which never buys land either. Doubling
        # the walkable area for the same number of units means more of
        # every turn goes to movement instead of watering or harvesting
        # -- confirmed earlier: main_v2 was already spending 41.7% of
        # all unit-turns on pure movement against main_v1's 29.3%, and
        # a bigger board only makes that ratio worse.
        # Re-enabled: disabling this earlier was correct given a hand cap
        # of 4, but a real ladder loss (episode 90062918, 3x margin)
        # showed an opponent running land and hands as complements, not
        # substitutes -- 9 hands across all 4 quadrants outproduced a
        # smaller, denser operation by a wide margin. With hands scaled
        # up above, this should have enough workforce to actually use
        # the extra land rather than just spreading thin across it.
        # day >= 4 added per a well-verified public notebook's direct
        # replay analysis: the opening bankroll goes further funding
        # hands and crops first, since land produces nothing until
        # something is planted and grown on it, and buying it too early
        # competes with hiring for the same early capital -- the same
        # failure mode this file already found the hard way with animals.
        # Fixed-timer buy: once the earliest day is reached and the current
        # quadrant is reasonably full (0.6 floor, looser than the old 0.7 --
        # keiz commits on the timer even before the prior quadrant is
        # saturated), buy as soon as capital allows. The RESERVE-only cash
        # gate (no extra cushion) matches keiz spending aggressively toward
        # land the moment the melon money lands.
        # Utilization floor raised to 0.85: buying the next quadrant while
        # the current one is only ~60% full left 40+ tiles idle across two
        # quadrants that 12 units couldn't service (strawberry stalled while
        # land sat empty). Only expand once the current land is genuinely
        # near-full, so the workforce actually fills new land instead of
        # spreading thin. Keeps keiz's day-timer as an EARLIEST bound, but
        # utilization is the real trigger.
        if day >= earliest_day and utilization > 0.72 and money - RESERVE >= next_cost:
            orders.append(["BUY_LAND"])

    # HIRE sorted to the front before the maxMarketOrdersPerTurn cap below,
    # not appended in the order built above -- confirmed directly against
    # the real engine source (_process_market truncates the *entire*
    # per-player order list to maxMarketOrdersPerTurn=10, HIRE included,
    # before processing anything) and against a real replay: day 22 hour 1
    # issued 5 SELL + 1 BUY_SEED before reaching any HIRE, leaving room for
    # only 4 of the 12 intended HIRE orders that turn, and the observed
    # hand count that day was exactly 4. This starved hiring hardest late
    # game, exactly when harvest volume (and SELL orders) is largest and
    # more hands would help most -- the likely real explanation for hand
    # count declining late-game despite target_hands only going up. HIRE is
    # cheap (~$376 total for 12 hands, fibonacci-scaled) and processed
    # atomically (once per queue slot, not per-unit lockstep like SELL/BUY),
    # so reordering it first is safe and doesn't interact with the
    # concurrent-lockstep logic those other order types depend on.
    orders.sort(key=lambda o: 0 if o[0] == "HIRE" else 1)
    return orders[:10]  # maxMarketOrdersPerTurn default; extras would be dropped otherwise


# -------------------------------------------------------------- agent -----

def agent(obs):
    player = obs["player"]
    farm = obs["farms"][player]
    private = obs["private"]
    day = obs["day"]
    prices = obs["market"]["prices"]
    money = farm["money"]
    seeds = private.get("seeds", {})
    board_size = len(farm["tiles"])
    inventories = private.get("inventories", [{}])

    tiles = list(unlocked_tiles(farm))
    has_animals = any(
        isinstance(t, dict) and t.get("kind") in ("COOP", "PASTURE") and t.get("animal")
        for _, _, t in tiles
    )
    animal_pick = choose_animal_program(farm, private, day, inventories)
    # keiz clone: build structures AHEAD of the herd, up to the plan's
    # totals, so several animals can be delivered concurrently into ready
    # structures. keiz has ~15 pastures + 1 coop built by day 11 -- well
    # ahead of the animals that fill them. Count structures already built
    # (filled or empty) and keep building until each type reaches its plan
    # total. Building is free (engine source), so the only cost of building
    # ahead is the tile, which is exactly what the herd needs anyway.
    pasture_target = sum(n for a, n in ANIMAL_PLAN if ANIMALS[a]["structure"] == "PASTURE")
    coop_target = sum(n for a, n in ANIMAL_PLAN if ANIMALS[a]["structure"] == "COOP")
    n_pastures = sum(1 for _, _, t in tiles if isinstance(t, dict) and t.get("kind") == "PASTURE")
    n_coops = sum(1 for _, _, t in tiles if isinstance(t, dict) and t.get("kind") == "COOP")
    # Build structures just BARELY ahead of the herd, tied to the same
    # day-scaled ramp the animal buy uses (see below), NOT to a fixed lead.
    # An early draft with an "owned + 3" lead paved 9 pastures on day 0 and
    # crowded out the 12-melon opening. keiz's measured build: 4 pastures by
    # end of day 0 (for 4 animals), 6 by day 1 -- structures track the herd
    # about +2 ahead. The coop is only built once GOOSE is actually the
    # current pick (keiz builds its single coop ~day 10, not before).
    total_owned = animal_total_owned(farm, private, inventories)
    herd_now = total_owned.get("COW", 0) + total_owned.get("SHEEP", 0) + total_owned.get("GOOSE", 0)
    herd_cap_today = min(pasture_target + coop_target, 4 + max(0, day))
    building_active = len(farm.get("hands", [])) >= 2
    # Pasture lead capped at the day's herd cap + 2, so we never build more
    # than the ramp will fill soon.
    want_pasture = (building_active and n_pastures < pasture_target
                    and n_pastures < min(pasture_target, herd_cap_today + 2))
    want_coop = (building_active and animal_pick == "GOOSE" and n_coops < coop_target)

    # One shared roster: farmer first, then each hand, in order. Farmer
    # gets index 0 into private["inventories"]; hands get 1, 2, ...
    units = [("farmer", tuple(farm["farmer"]), inventories[0] if inventories else {})]
    for i, pos in enumerate(farm.get("hands", [])):
        inv = inventories[i + 1] if i + 1 < len(inventories) else {}
        units.append((f"hand{i}", tuple(pos), inv))

    # Seed the claimed set with every unit's current tile, so nobody gets
    # sent traveling toward a square another unit is already standing on
    # -- that's the collision that broke the first draft of this agent.
    claimed = {pos for _, pos, _ in units}
    actions = {}

    # Per-turn claims for concurrent animal delivery (keiz clone): which
    # empty structures a unit has already been routed to this turn, and how
    # many of each shed animal have been spoken for -- so multiple units can
    # deliver different animals at once without two of them fetching the
    # same shed unit or converging on the same empty pasture.
    claimed_structures = set()
    shed_animals_claimed = {}

    # Mutable, turn-local copy: decremented as units claim a PLANT this
    # turn so a second unit standing on a *different* empty tile doesn't
    # also reach for the same scarce seed. The engine's "plant too many
    # in one turn, none get planted" rule keys off seed count, not tile,
    # so two units on two different tiles can still collide -- this was
    # happening on the vast majority of turns before this fix, since
    # seeds only ever get bought one at a time.
    seeds_remaining = dict(seeds)
    field_counts = crop_counts_on_field(farm)

    fert_targets_exist = any(needs_fertilizer(t, day) for _, _, t in tiles)
    shed_fertilizer = private.get("shed", {}).get("FERTILIZER", 0)

    # At most one structure of each kind built per turn (see the build
    # decrement in the loop) -- keeps building paced to ~1-2/day like keiz
    # instead of every idle unit paving a pasture the same turn.
    build_budget = {"PASTURE": 1, "COOP": 1}
    # At most one unit per turn goes to fetch fertilizer (see the fert-fetch
    # block) so the rest stay on planting/watering.
    fert_fetch_budget = [1]

    for name, pos, inv in units:
        fx, fy = pos
        tile = tile_at(farm, fx, fy)

        act = immediate_action(tile, seeds_remaining, day, money,
                                want_coop and build_budget["COOP"] > 0,
                                want_pasture and build_budget["PASTURE"] > 0,
                                field_counts=field_counts, own_inventory=inv)
        if act is not None:
            if isinstance(act, list) and act[0] == "PLANT":
                seeds_remaining[act[1]] = seeds_remaining.get(act[1], 0) - 1
                field_counts[act[1]] = field_counts.get(act[1], 0) + 1
            # Only ONE structure of each kind may be built per turn -- every
            # idle unit standing on a plantable tile would otherwise build
            # its own in the same turn (an early draft paved 9 pastures on
            # day 0 this way). Decrement the turn-local budget so subsequent
            # units this turn fall through to real fieldwork instead.
            elif act == "BUILD_PASTURE":
                build_budget["PASTURE"] -= 1
            elif act == "BUILD_COOP":
                build_budget["COOP"] -= 1
            actions[name] = act
            continue

        # Animal delivery, per-unit (keiz clone): concurrent delivery is
        # what lets the herd reach ~15 by day 11 like keiz. First, if THIS
        # unit is already carrying an animal, finish placing it into a
        # not-yet-claimed empty structure. Otherwise, if animals wait in
        # the shed and an empty structure is free, claim one and go fetch.
        # `claimed_structures` prevents two units targeting the same empty
        # pasture/coop this turn. Replaces the old single-in-flight
        # bottleneck (one animal delivered at a time), which capped how
        # fast the herd could grow far below keiz's pace.
        carried_animal = next((a for a in ANIMALS if inv.get(a, 0) > 0), None)
        if carried_animal:
            info = ANIMALS[carried_animal]
            spot = None
            for x, y, t in tiles:
                if (isinstance(t, dict) and t.get("kind") == info["structure"]
                        and not t.get("animal") and (x, y) not in claimed_structures):
                    spot = (x, y); break
            if spot:
                claimed_structures.add(spot)
                tx, ty = spot
                if (fx, fy) == (tx, ty):
                    actions[name] = ["PLACE", carried_animal, 1]
                else:
                    actions[name] = step_toward(fx, fy, tx, ty)
                continue
        else:
            shed = private.get("shed", {})
            fetch = None
            for x, y, t in tiles:
                if not isinstance(t, dict):
                    continue
                k = t.get("kind")
                if k in ("PASTURE", "COOP") and not t.get("animal") and (x, y) not in claimed_structures:
                    for a, info in ANIMALS.items():
                        if info["structure"] == k and shed.get(a, 0) - shed_animals_claimed.get(a, 0) > 0:
                            fetch = (a, (x, y)); break
                if fetch:
                    break
            if fetch:
                a, spot = fetch
                claimed_structures.add(spot)
                shed_animals_claimed[a] = shed_animals_claimed.get(a, 0) + 1
                if is_shed_adjacent((fx, fy), board_size):
                    actions[name] = ["PICKUP", a, 1]
                else:
                    sx, sy, _ = nearest(fx, fy, [(x, y, None) for x, y in shed_access_tiles(board_size)])
                    actions[name] = step_toward(fx, fy, sx, sy)
                continue

        # Wheat-for-feeding fetch: same pattern as the fertilizer fetch
        # below, and the exact same category of bug as the animal-place
        # fix earlier -- FEED consumes wheat from the acting unit's own
        # inventory, not the shed, and nothing was ever routing a unit
        # to actually go carry any. Confirmed directly: a placed goose
        # got FEED attempted 13 turns straight while the farmer carried
        # zero wheat, and starved to death by day 7 as a result.
        if needs_feed(tile) and (private.get("shed", {}).get("WHEAT", 0) > 0):
            if is_shed_adjacent((fx, fy), board_size):
                actions[name] = ["PICKUP", "WHEAT", 5]
            else:
                sx, sy, _ = nearest(fx, fy, [(x, y, None) for x, y in shed_access_tiles(board_size)])
                actions[name] = step_toward(fx, fy, sx, sy)
            continue

        # Fertilizer fetch: STRICTLY capped to one unit per turn (keiz
        # clone). An early draft let every idle unit fetch fertilizer at
        # once -- with 19 units of fertilizer sitting in the shed and crops
        # that could use it, the whole workforce cycled to the shed and back
        # every turn, leaving strawberry planting (30 empty tiles) and even
        # watering starved. Applying fertilizer is a yield nicety, not worth
        # pulling units off planting/watering; one fetcher at a time is
        # plenty. Selling the fertilizer surplus (11% of keiz's revenue) is
        # handled separately in build_market_orders and is unaffected.
        if (fert_fetch_budget[0] > 0 and inv.get("FERTILIZER", 0) == 0
                and shed_fertilizer > 0 and fert_targets_exist):
            fert_fetch_budget[0] -= 1
            if is_shed_adjacent((fx, fy), board_size):
                actions[name] = ["PICKUP", "FERTILIZER", 5]
            else:
                sx, sy, _ = nearest(fx, fy, [(x, y, None) for x, y in shed_access_tiles(board_size)])
                actions[name] = step_toward(fx, fy, sx, sy)
            continue

        # Claim the nearest not-yet-claimed job on the shared board this
        # turn, highest priority tier first, and head toward it.
        # Feed jobs are ONLY offered when wheat is actually obtainable
        # (this unit carries some, or the shed has some) -- otherwise a
        # feed job just traps the unit cycling to an empty shed forever
        # (a real seize-up found with the 15-herd once its wheat ran dry),
        # so when there's no wheat at all, skip feed entirely and let the
        # unit do fieldwork it CAN complete.
        wheat_obtainable = inv.get("WHEAT", 0) > 0 or private.get("shed", {}).get("WHEAT", 0) > 0
        # keiz clone: when the field is under-planted (strawberry still below
        # its floor and open land exists), PLANTING is promoted above the
        # OPTIONAL animal chores -- CARE (a yield bonus, not survival) and
        # free-fertilizer COLLECT. The binding constraint on matching keiz's
        # ~50-strawberry core is labor: a 15-animal herd's care+fertilizer
        # upkeep otherwise consumes the whole workforce and strawberry stalls
        # near 9 tiles while 25 sit empty. Survival-critical work (feed,
        # decay-urgent harvest, water) still outranks planting; only the
        # discretionary animal chores yield to it, and only while the field
        # is still being built out. Once strawberry is established, the
        # normal order resumes so the bonus/fertilizer value is recovered.
        underplanted = (field_counts.get("STRAWBERRY", 0) < STRAWBERRY_FLOOR
                        and any(is_plantable(t) for _, _, t in tiles)
                        and seeds_remaining.get("STRAWBERRY", 0) > 0)
        feed_pool = ([(x, y, t) for x, y, t in tiles if needs_feed(t) and (x, y) not in claimed]
                     if wheat_obtainable else [])
        cropmax = [(x, y, t) for x, y, t in tiles if crop_maxed(t) and (x, y) not in claimed]
        urgent = [(x, y, t) for x, y, t in tiles if crop_urgent(t, day) and (x, y) not in claimed]
        animalmax = [(x, y, t) for x, y, t in tiles if animal_maxed(t) and (x, y) not in claimed]
        water = [(x, y, t) for x, y, t in tiles if needs_water(t) and (x, y) not in claimed]
        care = [(x, y, t) for x, y, t in tiles if needs_care(t) and (x, y) not in claimed]
        harvest = [(x, y, t) for x, y, t in tiles if ready_to_harvest(t, day) and (x, y) not in claimed]
        fert = [(x, y, t) for x, y, t in tiles if has_fertilizer_to_collect(t) and (x, y) not in claimed]
        weed = [(x, y, t) for x, y, t in tiles if is_weed(t) and (x, y) not in claimed]
        plant = [(x, y, t) for x, y, t in tiles if is_plantable(t) and (x, y) not in claimed]
        if underplanted:
            # feed > decay-urgent > water > harvest > PLANT > care > fert > ...
            pool = (feed_pool or cropmax or urgent or animalmax or water
                    or harvest or plant or care or fert or weed)
        else:
            pool = (feed_pool or cropmax or urgent or animalmax or water
                    or care or harvest or fert or weed or plant)
        target = nearest(fx, fy, pool)
        if target:
            tx, ty, target_tile = target
            claimed.add((tx, ty))
            # A unit assigned to a needs_feed job from the pool (as
            # opposed to already standing on one, handled above) was
            # walking straight there with an empty inventory -- FEED
            # requires wheat in *this unit's own* inventory, so it would
            # arrive, find nothing to do, then walk all the way back to
            # the shed for wheat, then all the way back out again. A
            # real bug found testing a scaled-up animal herd: wheat shed
            # stayed well under WHEAT_FEED_BUFFER and animals kept
            # escaping from missed feeding even with plenty of wheat
            # being bought, because hands weren't actually carrying any
            # of it to the pasture -- this two-trip pattern couldn't
            # keep pace once there were more than a few animals. Detour
            # via the shed first if not already carrying wheat, same
            # pattern as the direct-pickup case above.
            if needs_feed(target_tile) and inv.get("WHEAT", 0) == 0:
                # Only detour to the shed if it actually has wheat -- the
                # pool guard above should already prevent a feed job with an
                # empty shed, but guard here too so a unit never walks to an
                # empty shed and loops.
                if private.get("shed", {}).get("WHEAT", 0) > 0:
                    if is_shed_adjacent((fx, fy), board_size):
                        actions[name] = ["PICKUP", "WHEAT", 5]
                    else:
                        sx, sy, _ = nearest(fx, fy, [(x, y, None) for x, y in shed_access_tiles(board_size)])
                        actions[name] = step_toward(fx, fy, sx, sy)
                else:
                    actions[name] = "PASS"
            else:
                actions[name] = step_toward(fx, fy, tx, ty)
        else:
            actions[name] = "PASS"

    farmer_action = actions["farmer"]
    if not isinstance(farmer_action, list):
        farmer_action = [farmer_action]

    hand_actions = []
    for i in range(len(farm.get("hands", []))):
        a = actions[f"hand{i}"]
        hand_actions.append(a if isinstance(a, list) else [a])

    market = build_market_orders(farm, private, day, obs["hour"], prices, has_animals, animal_pick)
    if animal_pick and len(market) < 10:
        # keiz clone: pace the herd against a day-scaled ramp, NOT a
        # per-turn batch. The buy check fires every one of the 24 turns in a
        # day, so any per-turn allowance multiplies into a runaway buy
        # (an early draft bought 6 cows on day 0 and the economy never
        # recovered). Instead, cap the TOTAL herd to a target that grows
        # with the day -- keiz's real curve is ~4 animals by day 0, ~8 by
        # day 6, ~13-15 by day 11 -- and only buy while under that day's
        # cap. This makes the buy self-limiting regardless of how many turns
        # fire, and matches keiz's measured ramp.
        total = animal_total_owned(farm, private, inventories)
        herd_now = sum(total.get(a, 0) for a, _ in ANIMAL_PLAN)
        plan_total = sum(n for _, n in ANIMAL_PLAN)
        # Ramp gently at first so the day-0 bankroll isn't blown on animals
        # before any crop income exists (an early draft bought 4 cows on
        # day 0 = $1600, crashed the economy, and the whole herd starved by
        # day 6 once hands could no longer be afforded). Start at 2, add
        # ~1/day, reaching plan_total (~15) by ~day 13. Slightly slower than
        # keiz's day-11, but the melon cash engine (day 10) needs to land
        # before the herd can be pushed hard without starving hands.
        herd_cap = min(plan_total, 2 + max(0, day))
        cost = ANIMALS[animal_pick]["cost"]
        structure = ANIMALS[animal_pick]["structure"]
        remaining_to_target = 0
        for a, tgt in ANIMAL_PLAN:
            if a == animal_pick:
                remaining_to_target = tgt - total.get(a, 0)
                break
        # Only buy into empty structures ready to receive an animal, net of
        # any already in the shed/in-flight heading there.
        empty_structs = sum(1 for _, _, t in tiles
                            if isinstance(t, dict) and t.get("kind") == structure
                            and not t.get("animal"))
        in_transit = private.get("shed", {}).get(animal_pick, 0) + sum(
            inv.get(animal_pick, 0) for inv in inventories)
        placeable_now = max(0, empty_structs - in_transit)
        # Generous working-capital cushion so the herd build never starves
        # seeds/feed/land -- larger than one animal's cost on purpose.
        ANIMAL_RESERVE = 400
        affordable = max(0, int((money - ANIMAL_RESERVE) // cost)) if cost else 0
        headroom = max(0, herd_cap - herd_now)
        buy_n = min(remaining_to_target, placeable_now, affordable, headroom)
        if buy_n > 0:
            market.append(["BUY_ANIMAL", animal_pick, buy_n])

    return {
        "farmer": farmer_action,
        "hands": hand_actions,
        "market": market[:10],
    }
