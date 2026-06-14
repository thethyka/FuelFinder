#!/usr/bin/env python3
"""Quick end-to-end check of nationwide fuel support + routing.

Run: python3 test_vic.py
"""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))
from servo import get_stations, find_best_station, route_bbox

# A short Melbourne route: Footscray -> Melbourne CBD (~6 km).
path = [
    [-37.8000, 144.9000],  # Footscray-ish
    [-37.8100, 144.9300],
    [-37.8136, 144.9631],  # Flinders St, CBD
]

print("== bbox ==", route_bbox(path, 0.1))

print("\n== VIC fetch (auto-detect -> PetrolSpy) ==")
vic = get_stations(path)
print("stations:", len(vic))
for s in vic[:5]:
    print(f"  {s['brand']:14} {s['price']:6} c/L  {s['address']}")

print("\n== routing: low tank forces a stop ==")
res = find_best_station(
    path,
    vic,
    efficiency_l_per_100km=10.0,
    capacity_litres=50.0,
    current_litres=2.0,
    has_rac=False,
    has_woolies=False,
)
print("status:", res.get("status"))
if res.get("status") == "ok":
    st = res["station"]
    print(f"  picked: {st['brand']} @ {st['address']}")
    print(
        f"  {st['price']} c/L  detour {st['diversion_km']} km  est ${st['cost_cents']/100:.2f}"
    )

print("\n== routing: full tank => no stop ==")
res2 = find_best_station(path, vic, 10.0, 50.0, 45.0, False, False)
print("status:", res2.get("status"), "tank_at_dest:", res2.get("tank_at_dest"))

# --- Fuel-type toggle: each type returns its own prices (VIC + WA) -----------
print("\n== fuel types (VIC -> PetrolSpy) ==")
for f in ("91", "95", "98", "DIESEL"):
    st = get_stations(path, fuel=f)
    cheapest = min((s["price"] for s in st), default=None)
    print(f"  {f:7} -> {len(st):3} stations | cheapest {cheapest} c/L")

print("\n== fuel types (WA -> FuelWatch) ==")
wa_path = [[-31.95, 115.86], [-31.98, 115.90]]
for f in ("91", "95", "98", "DIESEL"):
    st = get_stations(wa_path, fuel=f)
    cheapest = min((s["price"] for s in st), default=None)
    print(f"  {f:7} -> {len(st):3} stations | cheapest {cheapest} c/L")


# --- Nationwide coverage: auto-detect picks the right source per state -------
print("\n== nationwide auto-detect (one point per capital) ==")
cities = {
    "Perth (WA -> FuelWatch)": [[-31.95, 115.86], [-31.98, 115.90]],
    "Adelaide (SA -> PetrolSpy)": [[-34.93, 138.60], [-34.90, 138.62]],
    "Sydney (NSW -> PetrolSpy)": [[-33.87, 151.21], [-33.85, 151.22]],
    "Brisbane (QLD -> PetrolSpy)": [[-27.47, 153.02], [-27.45, 153.03]],
    "Hobart (TAS -> PetrolSpy)": [[-42.88, 147.33], [-42.86, 147.34]],
}
for name, p in cities.items():
    try:
        n = len(get_stations(p))
        print(f"  {name:30} -> {n} stations")
    except Exception as e:
        print(f"  {name:30} -> ERROR: {e}")
