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
| `RESERVE_L` | `5.0 L` | Safety reserve. We never *plan* to drop below this. |
| `MAX_DETOUR_KM` | `5.0 km` | A candidate station may add at most this much extra driving. |
| `BBOX_PAD_DEG` | `0.1°` | Lat/lon padding for the station pre-filter bounding box. |

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
| `too_far` | A stop is required but **no** reachable station can complete the trip on one full tank. | No waypoint. Show "This trip is longer than one tank — multi-stop coming soon" (§6). |
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

---

## 6. Out of scope (documented next phase): multi-stop

When a single full tank cannot span the trip (`too_far`), the trip needs a **chain**
of refuels. The intended future algorithm:

1. Walk the route from the origin.
2. At each point where the projected tank would fall to `RESERVE_L`, search for
   the cheapest station within `MAX_DETOUR_KM` of the *reachable* window before
   that point.
3. Refuel (to `capacity_litres`), continue, repeat until the destination is
   within range.
4. Return an ordered list of stops; the frontend inserts them as ordered
   waypoints.

Until then, `too_far` is surfaced honestly rather than silently picking one
inadequate station.

---

## 7. Frontend route-drawing rules

These fix *"inconsistent route drawing"* and *"sometimes routes to old
locations."*

1. **Single source of truth for the base route.** `userRoutePoints` holds the
   pure origin→destination geometry. It is updated **only** from `route` events
   fired while **no** fuel waypoint of ours is present. It is the only thing ever
   sent to the API.
2. **At most one fuel waypoint**, always inserted at waypoint index `0` (the
   first intermediate waypoint). Never hardcode other indices.
3. **Replacing a stop** (recalculate while a stop exists) removes the old
   waypoint first, then adds the new one on the next settled `route` event — so
   the two async route requests never race.
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
| Cross-country | 10 L / 50 / 9 L/100km | 900 km | `too_far` — one full 50 L tank (~555 km) can't span it. |
| Stop needed but remote highway | 6 L / 60 / 9 L/100km | 140 km | `unreachable` if no station within 5 km of the route. |
