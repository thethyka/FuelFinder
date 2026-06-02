from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import xml.etree.ElementTree as ET
from math import radians, cos, sin, asin, sqrt


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371  # km


def fetch_stations():
    req = urllib.request.Request(
        "https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        root = ET.fromstring(resp.read())

    stations = []
    for item in root.findall("./channel/item"):
        lat   = item.findtext("latitude")
        lon   = item.findtext("longitude")
        price = item.findtext("price")
        brand = item.findtext("brand")
        addr  = item.findtext("address")
        loc   = item.findtext("location")
        if not (lat and lon and price and brand):
            continue
        stations.append({
            "lat":     float(lat),
            "lon":     float(lon),
            "price":   float(price),
            "brand":   brand,
            "address": f"{addr}, {loc}" if addr and loc else (addr or loc or ""),
        })
    return stations


RAC_BRANDS    = {"Puma", "Caltex", "Better Choice"}
WOOLIES_BRANDS = {"Ampol", "EG Ampol", "Caltex", "Caltex Woolworths"}

# See ROUTING_SPEC.md §2 for the meaning of these constants.
RESERVE_L     = 5.0    # never plan to drop below this many litres
MAX_DETOUR_KM = 5.0    # a station may add at most this much extra driving
BBOX_PAD_DEG  = 0.1    # lat/lon padding for the station pre-filter


def cumulative_distances(path):
    """Distance from the origin to each path point, in km (ROUTING_SPEC §4.2)."""
    cum = [0.0] * len(path)
    for i in range(1, len(path)):
        a, b = path[i - 1], path[i]
        cum[i] = cum[i - 1] + haversine(a[0], a[1], b[0], b[1])
    return cum


def find_best_station(path, stations, efficiency_l_per_100km,
                      capacity_litres, current_litres, has_rac, has_woolies):
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

    cum     = cumulative_distances(path)
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
        s for s in stations
        if lat_min <= s["lat"] <= lat_max and lon_min <= s["lon"] <= lon_max
    ]

    # Track why candidates were rejected so we can return a precise status.
    saw_reachable = False  # at least one station within reach + detour
    best = None
    best_cost = float("inf")
    best_detour = float("inf")

    for s in nearby:
        price = s["price"]
        if has_rac     and s["brand"] in RAC_BRANDS:     price -= 4
        if has_woolies and s["brand"] in WOOLIES_BRANDS: price -= 4

        # §4.2b: route segment with the smallest detour for this station.
        min_div = float("inf")
        ins_i   = None
        d_to_at_ins = None
        for i in range(len(path) - 1):
            a, b   = path[i], path[i + 1]
            seg    = haversine(a[0], a[1], b[0], b[1])
            d_to   = haversine(a[0], a[1], s["lat"], s["lon"])
            d_from = haversine(s["lat"], s["lon"], b[0], b[1])
            div    = d_to + d_from - seg
            if div < min_div:
                min_div     = div
                ins_i       = i
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
        litres_to_buy   = max(0.0, capacity_litres - tank_at_station)
        detour_fuel_l   = min_div / km_per_l
        cost            = (litres_to_buy + detour_fuel_l) * price

        if cost < best_cost or (cost == best_cost and min_div < best_detour):
            best_cost   = cost
            best_detour = min_div
            best = {
                "address":         s["address"],
                "brand":           s["brand"],
                "price":           s["price"],
                "effective_price": round(price, 1),
                "lat":             s["lat"],
                "lon":             s["lon"],
                "diversion_km":    round(min_div, 2),
                "litres_to_buy":   round(litres_to_buy, 1),
                "cost_cents":      round(cost, 1),
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
            body   = json.loads(self.rfile.read(length))

            path            = body["path"]               # [[lat, lon], ...]
            efficiency      = float(body["efficiency"])   # L/100km (UI units)
            capacity_litres = float(body["capacity"])     # max tank size
            current_litres  = float(body["current_tank"])
            # Frontend sends 0 when checkbox is checked (has card), 1 when unchecked
            has_rac    = int(body.get("RAC",    1)) == 0
            has_woolies = int(body.get("Woolies", 1)) == 0

            stations = fetch_stations()
            result   = find_best_station(path, stations, efficiency, capacity_litres,
                                         current_litres, has_rac, has_woolies)

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
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        pass  # suppress default request logging in Vercel
