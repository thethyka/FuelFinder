# FuelFinder Routing Spec

This document defines **exactly** how FuelFinder decides whether to insert a fuel
stop, which station it picks, and how the route is drawn. The code in
`api/servo.py` and `Website/script.js` is expected to conform to this spec. If
behaviour and spec disagree, the spec is the source of truth — fix the code.

---

## 1. Inputs

| Input | Source | Unit | Notes |
|-------|--------|------|-------|
| Route (origin → destination) | Mapbox Directions plugin | list of `[lat, lon]` points | The user's own A→B route, **never** including a fuel waypoint. |
| Fuel efficiency | `#fuel-efficiency` | **L/100km** | As labelled in the UI (e.g. `10`). |
| Tank Capacity (`capacity_litres`) | `#tank-capacity` | litres | Max tank size. A stop fills the tank to this. |
| Current Tank (`current_litres`) | `#current-tank` | litres | Fuel in the tank right now. |
| RAC member | checkbox | bool | Discount eligibility. |
| Woolworths Rewards | checkbox | bool | Discount eligibility. |
| Live station prices | FuelWatch RSS | cents/L | Fetched server-side per request. |

### 1.1 Units — IMPORTANT

Efficiency is entered as **litres per 100 km**. All range maths uses
**km per litre**, so the backend must convert exactly once:

```
km_per_l = 100 / efficiency_l_per_100km
```

> Historical bug: the old backend used the raw L/100km number directly as if it
> were km/L. The two only coincide at the value `10`, so every other efficiency
> produced wrong range/cost. This conversion is now mandatory.

---

## 2. Constants

| Name | Value | Meaning |
|------|-------|---------|
| `RESERVE_L` | `3.0 L` | Safety reserve. We never *plan* to drop below this. |
| `MAX_DETOUR_KM` | `5.0 km` | A candidate station may add at most this much extra driving. |
| `BBOX_PAD_DEG` | `0.1°` | Lat/lon padding for the station pre-filter bounding box. |
| `MAX_MULTI_STOPS` | `5` | A multi-stop chain may contain at most this many refuels (§6). |

---

## 3. Core decision: do we even need a stop?

This is the headline rule and the fix for *"it always routes me to a station
even when I don't need one."*

```
trip_km          = total length of the A→B route
fuel_for_trip_L  = trip_km / km_per_l
tank_at_dest_L   = current_litres - fuel_for_trip_L
```

- **If `tank_at_dest_L >= RESERVE_L` → NO STOP.**
  The car reaches the destination with at least the reserve to spare. The
  backend returns `status: "no_stop_needed"` and the frontend draws the plain
  A→B route with **no** fuel waypoint and **no** marker.

- **Otherwise a stop is required** — proceed to station selection (§4).

There is **no** "stop anyway because it's cheaper" behaviour. A stop is inserted
only when the tank would otherwise fall below the reserve before arrival.

---

## 4. Station selection (only runs when a stop is required)

### 4.1 Pre-filter

Build a bounding box around the whole route, padded by `BBOX_PAD_DEG` on each
side, and keep only stations inside it. This is a cheap cull before the per-station
maths.

### 4.2 Per-station evaluation

For each candidate station `s`:

**a. Effective price** (cents/L):
```
price = base_price
if has_rac     and brand in RAC_BRANDS:     price -= 4
if has_woolies and brand in WOOLIES_BRANDS: price -= 4
```

**b. Best insertion point & detour.** Find the route segment `(A_i, B_i)` where
inserting the station adds the least distance:
```
detour_i = dist(A_i → s) + dist(s → B_i) − dist(A_i → B_i)
```
Take the segment with the smallest `detour_i`. Call it `detour_km`, and let
`dist_to_station` = (cumulative route distance to `A_i`) + `dist(A_i → s)`.

**c. Gates** — the station is discarded unless **all** hold:

| Gate | Condition | Rationale |
|------|-----------|-----------|
| Detour cap | `detour_km <= MAX_DETOUR_KM` | Not absurdly out of the way. |
| Reachable | `dist_to_station / km_per_l <= current_litres` | We can physically reach it without running dry. |
| Completes trip | `dist_station_to_dest / km_per_l <= capacity_litres - RESERVE_L` | After filling to capacity, we reach the destination still holding the reserve. |

where `dist_station_to_dest = (trip_km + detour_km) − dist_to_station`.

> The **Completes trip** gate is what enforces the single-stop scope: if *no*
> reachable station lets a full tank span the remaining distance, the trip needs
> more than one tank — see §6.

**d. Cost** of choosing this station. A stop always fills the tank to capacity:
```
tank_at_station = current_litres − dist_to_station / km_per_l
litres_to_buy   = max(0, capacity_litres − tank_at_station)
detour_fuel_L   = detour_km / km_per_l
cost_cents      = (litres_to_buy + detour_fuel_L) × price
```
The cost rewards a low effective price and penalises detours (the extra fuel the
detour burns, valued at that station's price). It does **not** add a flat
"distance to destination" term — every car must drive to the destination anyway,
so that term is not a differentiator and previously biased the result.

### 4.3 Winner

Among all stations passing the gates, pick the one with the **lowest
`cost_cents`**. Ties broken by smaller `detour_km`.

---

## 5. Outcomes (API contract)

The API returns one of these statuses:

| `status` | When | Frontend behaviour |
|----------|------|--------------------|
| `no_stop_needed` | §3 says the tank reaches the destination with reserve. | Plain A→B route, no marker. Show "No stop needed — arrive with ~`X` L". |
| `ok` | A station passed all gates. | Insert exactly one fuel waypoint at the station, drop a marker with brand / cost / detour. |
| `multi_stop` | A single tank can't span the trip, but a chain of ≤ `MAX_MULTI_STOPS` refuels can (§6). | Insert one fuel waypoint per stop, in route order, and drop a numbered marker at each. |
| `too_far` | A stop is required and **no** chain of ≤ `MAX_MULTI_STOPS` reachable stations can complete the trip. | No waypoint. Show "Too far, even with multiple stops". |
| `unreachable` | A stop is required, the trip *is* within one tank, but no station is within reach + detour limits. | No waypoint. Show "No suitable station found near this route". |

### 5.1 `ok` payload

```jsonc
{
  "status": "ok",
  "station": {
    "address": "123 Example Rd, Suburb",
    "brand": "Puma",
    "price": 189.9,            // base price, cents/L
    "effective_price": 185.9,  // after discounts, cents/L
    "lat": -31.95,
    "lon": 115.86,
    "diversion_km": 1.2,
    "litres_to_buy": 32.4,
    "cost_cents": 6023.0
  },
  "trip_km": 412.0,
  "tank_at_dest_no_stop": 1.3   // why a stop was required
}
```

> Backwards note: the old API returned a bare array
> `[address, diversion, cost, [lat, lon]]`. The frontend in this repo is updated
> to the object form above. Any other consumer must migrate.

### 5.2 `multi_stop` payload

```jsonc
{
  "status": "multi_stop",
  "stations": [
    {
      "address": "...", "brand": "...", "price": 189.9,
      "effective_price": 185.9, "lat": -31.9, "lon": 115.8,
      "diversion_km": 1.2, "litres_to_buy": 48.0, "cost_cents": 8920.0
    }
    // ...one object per stop, in route order (origin → destination)
  ],
  "trip_km": 1010.0
}
```

Each stop fills to capacity, so it reports a single `cost_cents` (not the
`min`/`max` range a single stop uses, which exists because a lone stop may buy
less than a full tank).

---

## 6. Multi-stop: when one tank can't span the trip

When no single full tank can complete the trip, the single-stop search of §4
yields no winner but *does* see reachable stations. Rather than return `too_far`
immediately, we attempt a **greedy forward-walk** chain of refuels. This runs
only as a fallback — §3 and §4 are untouched, so trips solvable with zero or one
stop never reach this code.

### 6.1 The forward walk

Track `pos_km` (distance along the route of the last stop, or `0` at the origin)
and `fuel` (litres in the tank at that point; `current_litres` at the origin).
Repeat, up to `MAX_MULTI_STOPS` times:

1. **Done?** `usable_range = (fuel − RESERVE_L) × km_per_l`. If
   `pos_km + usable_range ≥ trip_km`, the destination is in range — stop.
2. **Search window — the back half.** Only consider stations whose
   `dist_to_station` (cumulative route distance, §4.2b) falls in
   `[pos_km + usable_range/2, pos_km + usable_range]`. Searching the *back* half
   of the reachable range avoids stopping too early and needing an extra stop.
3. **Gates.** A candidate must pass the detour cap (`detour_km ≤ MAX_DETOUR_KM`)
   and be reachable from the current position
   (`(dist_to_station − pos_km) / km_per_l ≤ fuel − RESERVE_L`).
4. **Pick.** Among survivors, choose the lowest `cost_cents`, ties broken by
   smaller `detour_km`. Cost is `(litres_to_fill + detour_fuel) × effective_price`,
   where a stop always fills to `capacity_litres`.
5. **Advance.** Append the stop, set `fuel = capacity_litres`,
   `pos_km = dist_to_station`, and loop.

### 6.2 Outcomes

- If the loop ends with the destination in range, return `multi_stop` (§5.2).
- If any iteration finds **no** candidate in its window, or the cap is hit before
  the destination is in range, the chain fails and the API returns `too_far`.
  `too_far` now means "not even a multi-stop chain works", surfaced honestly
  rather than picking inadequate stops.

> The walk is greedy, not globally cost-optimal: it minimises stops first
> (back-half heuristic) and picks the cheapest station within each window
> independently. A globally cheapest chain — e.g. buying partial fills to exploit
> a cheaper station later — is intentionally out of scope.

---

## 7. Frontend route-drawing rules

These fix *"inconsistent route drawing"* and *"sometimes routes to old
locations."*

1. **Single source of truth for the base route.** `userRoutePoints` holds the
   pure origin→destination geometry. It is updated **only** from `route` events
   fired while **no** fuel waypoint of ours is present. It is the only thing ever
   sent to the API.
2. **Our fuel waypoints occupy indices `0..n-1`** in route order (one for a
   single stop, several for a `multi_stop` chain). Adds append in order; removes
   always pull index `0` until none remain.
3. **One waypoint op per settled `route` event.** The Directions plugin fires an
   async route request per `addWaypoint`/`removeWaypoint`, so ops are queued and
   drained one at a time — the requests are strictly sequenced and never race.
   Replacing the current stops (recalculate) enqueues all removals first, then
   the new adds.
4. **Invalidate on edit.** If the user changes the origin or destination (Mapbox
   `origin` / `destination` events), immediately remove our fuel waypoint and
   marker and clear any queued stop. The next route the user draws is captured
   fresh — stale stops can never linger.
5. The **Calculate** button is disabled while a route request is in flight and
   re-enabled on the next settled `route` event.
6. **Reset** removes the fuel waypoint and marker and returns to the plain A→B
   route.

---

## 8. Worked examples

| Scenario | current / capacity / eff | trip | Expected |
|----------|--------------------------|------|----------|
| Short hop, plenty of fuel | 40 L / 60 / 8 L/100km | 60 km | `no_stop_needed` (arrive ~32.5 L). |
| Commute, would dip below reserve | 8 L / 60 / 8 L/100km | 120 km | Stop required; pick cheapest reachable station that completes the trip. |
| Cross-country, stations en route | 10 L / 50 / 9 L/100km | 900 km | `multi_stop` — one full 50 L tank (~555 km) can't span it, but a chain of refuels can. |
| Cross-country, sparse coverage | 10 L / 50 / 9 L/100km | 900 km | `too_far` if a refuel window has no reachable station, or the chain would exceed `MAX_MULTI_STOPS`. |
| Stop needed but remote highway | 6 L / 60 / 9 L/100km | 140 km | `unreachable` if no station within 5 km of the route. |
