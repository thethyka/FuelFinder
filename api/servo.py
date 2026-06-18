from http.server import BaseHTTPRequestHandler
import gzip
import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from math import radians, cos, sin, asin, sqrt, ceil


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(
        radians, [float(lat1), float(lon1), float(lat2), float(lon2)]
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371  # km


def route_bbox(path, pad=0.0):
    """Lat/lon bounding box of a route, optionally padded by ``pad`` degrees."""
    lats = [p[0] for p in path]
    lons = [p[1] for p in path]
    return (min(lats) - pad, max(lats) + pad, min(lons) - pad, max(lons) + pad)


# --- Fuel types --------------------------------------------------------------
#
# Both data sources support per-fuel-type prices, but they name them differently:
#   - FuelWatch takes a numeric ``Product`` query param.
#   - PetrolSpy returns a price dict keyed by its own fuel codes.
# This table is the single source of truth mapping our canonical keys to both.
# Keys here are what the frontend sends as ``fuel``; "91" is the default.
FUEL_TYPES = {
    "91": {"label": "Unleaded 91", "fw_product": 1, "ps_key": "U91", "vic_key": "U91"},
    "95": {"label": "Premium 95", "fw_product": 2, "ps_key": "U95", "vic_key": "P95"},
    "98": {"label": "Premium 98", "fw_product": 6, "ps_key": "U98", "vic_key": "P98"},
    "DIESEL": {"label": "Diesel", "fw_product": 4, "ps_key": "DIESEL", "vic_key": "DSL"},
}
DEFAULT_FUEL = "91"


def _normalise_fuel(fuel):
    """Return a valid canonical fuel key, falling back to the default."""
    fuel = (fuel or DEFAULT_FUEL).upper()
    return fuel if fuel in FUEL_TYPES else DEFAULT_FUEL


# --- WA: FuelWatch (official government RSS) --------------------------------


def fetch_stations_wa(bbox=None, fuel=DEFAULT_FUEL):
    """Live WA prices from FuelWatch for a given fuel. ``bbox`` is accepted for a
    uniform interface but FuelWatch returns the whole state, so it is ignored."""
    product = FUEL_TYPES[_normalise_fuel(fuel)]["fw_product"]
    req = urllib.request.Request(
        f"https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS?Product={product}",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        root = ET.fromstring(resp.read())

    stations = []
    for item in root.findall("./channel/item"):
        lat = item.findtext("latitude")
        lon = item.findtext("longitude")
        price = item.findtext("price")
        brand = item.findtext("brand")
        addr = item.findtext("address")
        loc = item.findtext("location")
        if not (lat and lon and price and brand):
            continue
        stations.append(
            {
                "lat": float(lat),
                "lon": float(lon),
                "price": float(price),
                "brand": brand,
                "address": f"{addr}, {loc}" if addr and loc else (addr or loc or ""),
            }
        )
    return stations


# Backwards-compatible alias (older callers import ``fetch_stations``).
fetch_stations = fetch_stations_wa


# --- VIC (and the rest of AU): PetrolSpy crowd-sourced feed -----------------
#
# Victoria has no FuelWatch equivalent, so there is no single official price
# API. PetrolSpy aggregates live, crowd-sourced prices nationwide and exposes a
# bounding-box JSON endpoint, which pairs naturally with our route bbox.

# PetrolSpy returns UPPERCASE brand codes; map the common ones to readable names
# (and to the names our discount sets in WOOLIES_BRANDS expect, e.g. "Ampol").
PETROLSPY_BRANDS = {
    "SEVENELEVEN": "7-Eleven",
    "AMPOL": "Ampol",
    "EGAMPOL": "EG Ampol",
    "CALTEX": "Caltex",
    "BP": "BP",
    "SHELL": "Shell",
    "COLES": "Reddy Express",
    "COLESEXPRESS": "Reddy Express",
    "REDDY": "Reddy Express",
    "UNITED": "United",
    "LIBERTY": "Liberty",
    "METRO": "Metro",
    "MOBIL": "Mobil",
    "PEARL": "Pearl Energy",
    "PUMA": "Puma",
    "VIBE": "Vibe",
    "COSTCO": "Costco",
    "SPEEDWAY": "Speedway",
    "X": "X Convenience",
    "OTR": "OTR",
}


def _petrolspy_price(prices, ps_key):
    """Price (cents/L) for the requested PetrolSpy fuel key, or None if the
    station doesn't report it."""
    if not isinstance(prices, dict):
        return None
    entry = prices.get(ps_key)
    if isinstance(entry, dict) and entry.get("amount"):
        return float(entry["amount"])
    return None


# PetrolSpy's /station/box endpoint returns HTTP 500 when a box would yield
# too many stations (~500), which happens on long routes and dense metros.
# We split the route into corridor boxes this wide (degrees, either axis) and
# then adaptively quarter any box that still 500s (see _fetch_petrolspy_adaptive).
PETROLSPY_MAX_BOX_DEG = 1.0
# Stop subdividing once a box is this small on both axes; an irreducibly dense
# cell is dropped rather than failing the whole route (won't happen in practice).
PETROLSPY_MIN_BOX_DEG = 0.05


def _split_bbox(bbox, max_deg=PETROLSPY_MAX_BOX_DEG):
    """Yield sub-boxes no larger than ``max_deg`` on either axis."""
    lat_min, lat_max, lon_min, lon_max = bbox
    n_lat = max(1, ceil((lat_max - lat_min) / max_deg))
    n_lon = max(1, ceil((lon_max - lon_min) / max_deg))
    lat_step = (lat_max - lat_min) / n_lat
    lon_step = (lon_max - lon_min) / n_lon
    for i in range(n_lat):
        for j in range(n_lon):
            yield (
                lat_min + i * lat_step,
                lat_min + (i + 1) * lat_step,
                lon_min + j * lon_step,
                lon_min + (j + 1) * lon_step,
            )


def _fetch_petrolspy_box(bbox, ps_key):
    """Fetch one PetrolSpy bounding box and parse it into station dicts."""
    lat_min, lat_max, lon_min, lon_max = bbox
    url = (
        "https://petrolspy.com.au/webservice-1/station/box"
        f"?neLat={lat_max}&neLng={lon_max}&swLat={lat_min}&swLng={lon_min}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    data = json.loads(raw)

    stations = []
    for s in data.get("message", {}).get("list", []):
        loc = s.get("location") or {}
        lat = loc.get("y")
        lon = loc.get("x")
        price = _petrolspy_price(s.get("prices"), ps_key)
        if lat is None or lon is None or price is None:
            continue
        code = (s.get("brand") or "").upper()
        brand = PETROLSPY_BRANDS.get(code, (s.get("brand") or "Unknown").title())
        addr = ", ".join(p for p in (s.get("address"), s.get("suburb")) if p)
        stations.append(
            {
                "id": s.get("id"),
                "lat": float(lat),
                "lon": float(lon),
                "price": price,
                "brand": brand,
                "address": addr or s.get("name", ""),
            }
        )
    return stations


def _fetch_petrolspy_adaptive(box, ps_key):
    """Fetch a box, recursively quartering it when PetrolSpy 500s.

    The endpoint fails when a box would return too many stations, so we can't
    pick one box size that works everywhere (dense metro vs. rural). Instead we
    try a box and, on a 500, split it into four quadrants and retry each.
    """
    try:
        return _fetch_petrolspy_box(box, ps_key)
    except urllib.error.HTTPError as e:
        if e.code != 500:
            raise
    lat_min, lat_max, lon_min, lon_max = box
    if (
        lat_max - lat_min <= PETROLSPY_MIN_BOX_DEG
        and lon_max - lon_min <= PETROLSPY_MIN_BOX_DEG
    ):
        return []
    mlat = (lat_min + lat_max) / 2
    mlon = (lon_min + lon_max) / 2
    out = []
    for q in (
        (lat_min, mlat, lon_min, mlon),
        (lat_min, mlat, mlon, lon_max),
        (mlat, lat_max, lon_min, mlon),
        (mlat, lat_max, mlon, lon_max),
    ):
        out.extend(_fetch_petrolspy_adaptive(q, ps_key))
    return out


def _route_corridor_boxes(path, pad, max_deg=PETROLSPY_MAX_BOX_DEG):
    """Boxes that follow the route corridor, each at most ``max_deg`` wide.

    Walks the path, accumulating points into a running box and emitting it
    (padded by ``pad``) whenever adding the next point would exceed the size
    limit on either axis. This covers only the road corridor, unlike gridding
    the full bounding rectangle, which queries many tiles far from the route.
    """
    inner = max(0.01, max_deg - 2 * pad)  # padded box stays within max_deg
    boxes = []
    lat_min = lat_max = path[0][0]
    lon_min = lon_max = path[0][1]
    for lat, lon in path[1:]:
        nlat_min, nlat_max = min(lat_min, lat), max(lat_max, lat)
        nlon_min, nlon_max = min(lon_min, lon), max(lon_max, lon)
        if (nlat_max - nlat_min) > inner or (nlon_max - nlon_min) > inner:
            boxes.append((lat_min - pad, lat_max + pad, lon_min - pad, lon_max + pad))
            lat_min = lat_max = lat
            lon_min = lon_max = lon
        else:
            lat_min, lat_max = nlat_min, nlat_max
            lon_min, lon_max = nlon_min, nlon_max
    boxes.append((lat_min - pad, lat_max + pad, lon_min - pad, lon_max + pad))
    return boxes


def fetch_stations_petrolspy(bbox, fuel=DEFAULT_FUEL, path=None):
    """Live prices from PetrolSpy (nationwide) for a given fuel.

    The upstream endpoint 500s on big areas, so a long route is broken into
    smaller boxes: corridor boxes that follow ``path`` when it's given,
    otherwise a grid over ``bbox``. Results are merged and de-duplicated.
    """
    ps_key = FUEL_TYPES[_normalise_fuel(fuel)]["ps_key"]

    if path:
        boxes = _route_corridor_boxes(path, BBOX_PAD_DEG)
    else:
        boxes = list(_split_bbox(bbox))

    merged = {}
    for box in boxes:
        for s in _fetch_petrolspy_adaptive(box, ps_key):
            # De-dup on station id, falling back to coordinates, since
            # neighbouring boxes overlap at their padded edges.
            key = s.get("id") or (s["lat"], s["lon"])
            merged.setdefault(key, s)

    stations = list(merged.values())
    for s in stations:
        s.pop("id", None)
    return stations


# Back-compat alias: this feed now covers all of Australia, not just VIC.
fetch_stations_vic = fetch_stations_petrolspy


# --- VIC: Fair Fuel Open Data API (government, 24h delay) -------------------

import time
import uuid
import os

VIC_API_BASE = "https://api.fuel.service.vic.gov.au/open-data/v1"
VIC_CONSUMER_ID = os.environ.get("VIC_FUEL_API_KEY", "")
VIC_CACHE_TTL = 21600  # 6 hours — data has a 24h delay, no point re-fetching sooner

_vic_cache = {"prices": None, "brands": None, "ts": 0}


def _fetch_vic_json(url, consumer_id):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FuelFinder/1.0",
            "x-consumer-id": consumer_id,
            "x-transactionid": str(uuid.uuid4()),
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _refresh_vic_cache():
    if not VIC_CONSUMER_ID:
        return
    now = time.time()
    if _vic_cache["prices"] and (now - _vic_cache["ts"]) < VIC_CACHE_TTL:
        return
    _vic_cache["prices"] = _fetch_vic_json(
        f"{VIC_API_BASE}/fuel/prices", VIC_CONSUMER_ID
    )
    _vic_cache["brands"] = _fetch_vic_json(
        f"{VIC_API_BASE}/fuel/reference-data/brands", VIC_CONSUMER_ID
    )
    _vic_cache["ts"] = now


def _vic_brands_map():
    brands_data = _vic_cache.get("brands") or {}
    return {b["id"]: b["name"] for b in brands_data.get("brands", [])}


def fetch_stations_vic_cached(fuel=DEFAULT_FUEL):
    _refresh_vic_cache()
    prices = _vic_cache.get("prices")
    if not prices:
        return []
    return parse_vic_stations(prices, _vic_brands_map(), fuel)


def parse_vic_stations(data, brands_map, fuel=DEFAULT_FUEL):
    """Turn a VIC API /fuel/prices response into our standard station dicts.

    ``brands_map`` is a dict of brandId -> brand name (from /reference-data/brands).
    Only stations with a valid location and an available price for the requested
    fuel type are included.
    """
    vic_key = FUEL_TYPES[_normalise_fuel(fuel)]["vic_key"]
    stations = []
    for detail in data.get("fuelPriceDetails", []):
        fs = detail.get("fuelStation", {})
        loc = fs.get("location") or {}
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            continue

        price = None
        for fp in detail.get("fuelPrices", []):
            if fp.get("fuelType") == vic_key and fp.get("isAvailable") and fp.get("price"):
                price = float(fp["price"])
                break
        if price is None:
            continue

        brand_id = fs.get("brandId", "")
        stations.append({
            "lat": float(lat),
            "lon": float(lon),
            "price": price,
            "brand": brands_map.get(brand_id, fs.get("name", "Unknown")),
            "address": fs.get("address", ""),
        })
    return stations


STATE_BOXES = {
    "WA":  (-35.5, -13.5, 112.5, 129.0),
    "VIC": (-39.5, -34.0, 140.9, 150.2),
}
COVERED_REGIONS = set(STATE_BOXES.keys())


def detect_state(lat, lon):
    for state, (lat_min, lat_max, lon_min, lon_max) in STATE_BOXES.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return state if state in COVERED_REGIONS else None
    return None


def get_stations(path, region=None, fuel=DEFAULT_FUEL):
    """Fetch live stations for a route from a licensed source.

    ``region`` is case-insensitive. When omitted it is inferred from the
    route midpoint via ``detect_state``. Routes outside covered regions
    return ``None``, which the handler reports as ``out_of_coverage``.
    ``fuel`` is a canonical key from ``FUEL_TYPES`` (defaults to ULP).
    """
    bbox = route_bbox(path, BBOX_PAD_DEG) if path else None

    if region:
        region = region.upper()
    elif path:
        mid = path[len(path) // 2]
        region = detect_state(mid[0], mid[1])
    else:
        region = None

    if region == "WA":
        return fetch_stations_wa(bbox, fuel)
    if region == "VIC":
        return fetch_stations_vic_cached(fuel)
    if os.environ.get("PETROLSPY_ENABLED"):
        return fetch_stations_petrolspy(bbox, fuel, path)
    return None


DISCOUNTS = {
    "WA": {
        "auto_club": {"cents": 4, "brands": {"Puma", "Caltex", "Better Choice"}},
        "woolies": {"cents": 4, "brands": {"Ampol", "EG Ampol", "Caltex", "Caltex Woolworths"}},
    },
    "VIC": {
        "auto_club": {"cents": 5, "brands": {"EG Ampol"}},
        "woolies": {"cents": 4, "brands": {"EG Ampol", "Ampol"}},
    },
}


def get_discount(region, brand, has_auto_club=False, has_woolies=False):
    region_discounts = DISCOUNTS.get(region, {})
    best = 0
    if has_auto_club:
        ac = region_discounts.get("auto_club", {})
        if brand in ac.get("brands", set()):
            best = max(best, ac["cents"])
    if has_woolies:
        w = region_discounts.get("woolies", {})
        if brand in w.get("brands", set()):
            best = max(best, w["cents"])
    return best

# See ROUTING_SPEC.md §2 for the meaning of these constants.
RESERVE_L = 3.0  # never plan to drop below this many litres
MAX_DETOUR_KM = 5.0  # a station may add at most this much extra driving
BBOX_PAD_DEG = 0.1  # lat/lon padding for the station pre-filter


def cumulative_distances(path):
    """Distance from the origin to each path point, in km (ROUTING_SPEC §4.2)."""
    cum = [0.0] * len(path)
    for i in range(1, len(path)):
        a, b = path[i - 1], path[i]
        cum[i] = cum[i - 1] + haversine(a[0], a[1], b[0], b[1])
    return cum


MAX_MULTI_STOPS = 5


def find_multi_stop_chain(
    path, stations, km_per_l, capacity_litres, current_litres,
    has_rac, has_woolies, region, cum, trip_km,
):
    stops = []
    fuel = current_litres
    pos_km = 0.0

    for _ in range(MAX_MULTI_STOPS):
        usable_range = (fuel - RESERVE_L) * km_per_l
        if pos_km + usable_range >= trip_km:
            break

        window_start = pos_km + usable_range / 2
        window_end = pos_km + usable_range

        best = None
        best_cost = float("inf")
        best_detour = float("inf")
        best_dist_to = 0.0

        for s in stations:
            min_div = float("inf")
            ins_i = None
            d_to_at_ins = None
            for i in range(len(path) - 1):
                a, b = path[i], path[i + 1]
                seg = haversine(a[0], a[1], b[0], b[1])
                d_to = haversine(a[0], a[1], s["lat"], s["lon"])
                d_from = haversine(s["lat"], s["lon"], b[0], b[1])
                div = d_to + d_from - seg
                if div < min_div:
                    min_div = div
                    ins_i = i
                    d_to_at_ins = d_to

            if min_div > MAX_DETOUR_KM:
                continue

            dist_to_station = cum[ins_i] + d_to_at_ins

            if dist_to_station < window_start or dist_to_station > window_end:
                continue

            km_from_pos = dist_to_station - pos_km
            if km_from_pos / km_per_l > fuel - RESERVE_L:
                continue

            discount = get_discount(region, s["brand"], has_rac, has_woolies)
            price = s["price"] - discount
            tank_at_station = fuel - km_from_pos / km_per_l
            litres_to_buy = max(0.0, capacity_litres - tank_at_station)
            detour_fuel = min_div / km_per_l
            cost = (litres_to_buy + detour_fuel) * price

            if cost < best_cost or (cost == best_cost and min_div < best_detour):
                best_cost = cost
                best_detour = min_div
                best_dist_to = dist_to_station
                best = {
                    "address": s["address"],
                    "brand": s["brand"],
                    "price": s["price"],
                    "effective_price": round(price, 1),
                    "lat": s["lat"],
                    "lon": s["lon"],
                    "diversion_km": round(min_div, 2),
                    "litres_to_buy": round(litres_to_buy, 1),
                    "cost_cents": round(cost, 1),
                }

        if best is None:
            return None

        stops.append(best)
        fuel = capacity_litres
        pos_km = best_dist_to

    if not stops:
        return None

    usable_range = (fuel - RESERVE_L) * km_per_l
    if pos_km + usable_range < trip_km:
        return None

    return {
        "status": "multi_stop",
        "stations": stops,
        "trip_km": round(trip_km, 1),
    }


def find_best_station(
    path,
    stations,
    efficiency_l_per_100km,
    capacity_litres,
    current_litres,
    has_rac,
    has_woolies,
    region=None,
):
    """Decide whether a fuel stop is needed and, if so, pick the best station.

    Returns a dict with a ``status`` field, per ROUTING_SPEC.md §5:
      - ``no_stop_needed`` : tank reaches the destination keeping the reserve.
      - ``ok``             : a station passed every gate (under ``station``).
      - ``too_far``        : stop needed but a full tank can't span the trip.
      - ``unreachable``    : stop needed, trip fits one tank, but no station
                             is within reach + detour limits.
      - ``no_route``       : fewer than two route points were supplied.
    """
    if len(path) < 2:
        return {"status": "no_route"}

    # Convert efficiency once: the UI collects L/100km, the maths needs km/L.
    if efficiency_l_per_100km <= 0:
        return {"status": "no_route"}
    km_per_l = 100.0 / efficiency_l_per_100km

    cum = cumulative_distances(path)
    trip_km = cum[-1]

    # --- §3: do we even need a stop? -------------------------------------
    tank_at_dest_no_stop = current_litres - trip_km / km_per_l
    if tank_at_dest_no_stop >= RESERVE_L:
        return {
            "status": "no_stop_needed",
            "trip_km": round(trip_km, 1),
            "tank_at_dest": round(tank_at_dest_no_stop, 1),
        }

    # --- §4.1: bounding-box pre-filter -----------------------------------
    lats = [p[0] for p in path]
    lons = [p[1] for p in path]
    lat_min, lat_max = min(lats) - BBOX_PAD_DEG, max(lats) + BBOX_PAD_DEG
    lon_min, lon_max = min(lons) - BBOX_PAD_DEG, max(lons) + BBOX_PAD_DEG
    nearby = [
        s
        for s in stations
        if lat_min <= s["lat"] <= lat_max and lon_min <= s["lon"] <= lon_max
    ]

    # Track why candidates were rejected so we can return a precise status.
    saw_reachable = False  # at least one station within reach + detour
    best = None
    best_cost = float("inf")
    best_detour = float("inf")

    for s in nearby:
        discount = get_discount(region, s["brand"], has_rac, has_woolies)
        price = s["price"] - discount

        # §4.2b: route segment with the smallest detour for this station.
        min_div = float("inf")
        ins_i = None
        d_to_at_ins = None
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            seg = haversine(a[0], a[1], b[0], b[1])
            d_to = haversine(a[0], a[1], s["lat"], s["lon"])
            d_from = haversine(s["lat"], s["lon"], b[0], b[1])
            div = d_to + d_from - seg
            if div < min_div:
                min_div = div
                ins_i = i
                d_to_at_ins = d_to

        # §4.2c gates
        if min_div > MAX_DETOUR_KM:
            continue  # absurdly out of the way

        dist_to_station = cum[ins_i] + d_to_at_ins
        if dist_to_station / km_per_l > current_litres - RESERVE_L:
            continue  # would hit reserve before reaching it

        saw_reachable = True

        dist_station_to_dest = (trip_km + min_div) - dist_to_station
        if dist_station_to_dest / km_per_l > capacity_litres - RESERVE_L:
            continue  # a full tank can't complete the trip from here

        # §4.2d cost — range from "just enough" to "fill to capacity".
        tank_at_station = current_litres - dist_to_station / km_per_l
        detour_fuel_l = min_div / km_per_l

        fuel_needed = dist_station_to_dest / km_per_l + RESERVE_L
        litres_min = max(0.0, fuel_needed - tank_at_station)
        litres_max = max(0.0, capacity_litres - tank_at_station)

        cost_min = (litres_min + detour_fuel_l) * price
        cost_max = (litres_max + detour_fuel_l) * price

        if cost_min < best_cost or (cost_min == best_cost and min_div < best_detour):
            best_cost = cost_min
            best_detour = min_div
            best = {
                "address": s["address"],
                "brand": s["brand"],
                "price": s["price"],
                "effective_price": round(price, 1),
                "lat": s["lat"],
                "lon": s["lon"],
                "diversion_km": round(min_div, 2),
                "litres_to_buy_min": round(litres_min, 1),
                "litres_to_buy_max": round(litres_max, 1),
                "cost_cents_min": round(cost_min, 1),
                "cost_cents_max": round(cost_max, 1),
            }

    if best is not None:
        return {
            "status": "ok",
            "station": best,
            "trip_km": round(trip_km, 1),
            "tank_at_dest_no_stop": round(tank_at_dest_no_stop, 1),
        }

    # Stop was needed but nothing qualified. Distinguish the two reasons:
    # if some station was reachable but none could complete the trip on one
    # fill, the trip is longer than a single tank (multi-stop territory).
    if saw_reachable:
        multi = find_multi_stop_chain(
            path, nearby, km_per_l, capacity_litres, current_litres,
            has_rac, has_woolies, region, cum, trip_km,
        )
        if multi is not None:
            return multi
        return {"status": "too_far", "trip_km": round(trip_km, 1)}
    return {"status": "unreachable", "trip_km": round(trip_km, 1)}


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))

            path = body["path"]  # [[lat, lon], ...]
            efficiency = float(body["efficiency"])  # L/100km (UI units)
            capacity_litres = float(body["capacity"])  # max tank size
            current_litres = float(body["current_tank"])
            # Frontend sends 0 when checkbox is checked (has card), 1 when unchecked
            has_rac = int(body.get("RAC", 1)) == 0
            has_woolies = int(body.get("Woolies", 1)) == 0
            region = body.get("region")  # optional "WA"/"VIC"; else auto-detected
            fuel = body.get("fuel", DEFAULT_FUEL)  # canonical key, defaults to ULP

            stations = get_stations(path, region, fuel)

            if not region and path:
                mid = path[len(path) // 2]
                region = detect_state(mid[0], mid[1])

            if stations is None:
                self._json(200, {"status": "out_of_coverage"})
                return
            result = find_best_station(
                path,
                stations,
                efficiency,
                capacity_litres,
                current_litres,
                has_rac,
                has_woolies,
                region=region,
            )
            result["region"] = region

            self._json(200, result)

        except (KeyError, ValueError) as e:
            self._json(400, {"error": f"Bad request: {e}"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        pass  # suppress default request logging in Vercel
