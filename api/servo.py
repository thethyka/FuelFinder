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
    "91": {"label": "Unleaded 91", "fw_product": 1, "ps_key": "U91"},
    "95": {"label": "Premium 95", "fw_product": 2, "ps_key": "U95"},
    "98": {"label": "Premium 98", "fw_product": 6, "ps_key": "U98"},
    "DIESEL": {"label": "Diesel", "fw_product": 4, "ps_key": "DIESEL"},
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


# Longitude of the WA/SA border (~129E). West of it we use the official WA
# FuelWatch feed; everywhere else in Australia uses PetrolSpy.
WA_BORDER_LON = 129.0

# WA-only launch: only the official WA FuelWatch feed is licensed for reuse.
# The PetrolSpy nationwide path is kept in this module but disabled — its terms
# prohibit automated access, so we don't ship it. Flip to False to re-enable
# nationwide coverage once a licensed non-WA source (e.g. VIC Servo Saver,
# NSW FuelCheck) is wired in.
WA_ONLY = True


def get_stations(path, region=None, fuel=DEFAULT_FUEL):
    """Fetch live stations for a route from a licensed source.

    Currently WA-only (``WA_ONLY``): only the official WA FuelWatch feed is used.
    Routes outside WA return ``None``, which the handler reports as
    ``out_of_coverage``.

    ``region`` is case-insensitive. When omitted it is inferred from the route's
    longitude (west of the WA border = WA).
    ``fuel`` is a canonical key from ``FUEL_TYPES`` (defaults to ULP).
    """
    bbox = route_bbox(path, BBOX_PAD_DEG) if path else None

    if region:
        region = region.upper()
    elif bbox is not None:
        mid_lon = (bbox[2] + bbox[3]) / 2
        region = "WA" if mid_lon < WA_BORDER_LON else "AU"
    else:
        region = "WA"

    if region == "WA":
        return fetch_stations_wa(bbox, fuel)
    if WA_ONLY:
        return None
    return fetch_stations_petrolspy(bbox, fuel, path)


RAC_BRANDS = {"Puma", "Caltex", "Better Choice"}
WOOLIES_BRANDS = {"Ampol", "EG Ampol", "Caltex", "Caltex Woolworths"}

# See ROUTING_SPEC.md §2 for the meaning of these constants.
RESERVE_L = 5.0  # never plan to drop below this many litres
MAX_DETOUR_KM = 5.0  # a station may add at most this much extra driving
BBOX_PAD_DEG = 0.1  # lat/lon padding for the station pre-filter


def cumulative_distances(path):
    """Distance from the origin to each path point, in km (ROUTING_SPEC §4.2)."""
    cum = [0.0] * len(path)
    for i in range(1, len(path)):
        a, b = path[i - 1], path[i]
        cum[i] = cum[i - 1] + haversine(a[0], a[1], b[0], b[1])
    return cum


def find_best_station(
    path,
    stations,
    efficiency_l_per_100km,
    capacity_litres,
    current_litres,
    has_rac,
    has_woolies,
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
        price = s["price"]
        if has_rac and s["brand"] in RAC_BRANDS:
            price -= 4
        if has_woolies and s["brand"] in WOOLIES_BRANDS:
            price -= 4

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
        if dist_to_station / km_per_l > current_litres:
            continue  # would run dry before reaching it

        saw_reachable = True

        dist_station_to_dest = (trip_km + min_div) - dist_to_station
        if dist_station_to_dest / km_per_l > capacity_litres - RESERVE_L:
            continue  # a full tank can't complete the trip from here

        # §4.2d cost — a stop fills the tank to capacity.
        tank_at_station = current_litres - dist_to_station / km_per_l
        litres_to_buy = max(0.0, capacity_litres - tank_at_station)
        detour_fuel_l = min_div / km_per_l
        cost = (litres_to_buy + detour_fuel_l) * price

        if cost < best_cost or (cost == best_cost and min_div < best_detour):
            best_cost = cost
            best_detour = min_div
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
            if stations is None:
                # Route lies outside our licensed coverage (WA-only for now).
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
            )

            # See ROUTING_SPEC.md §5 — the frontend switches on result["status"].
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
