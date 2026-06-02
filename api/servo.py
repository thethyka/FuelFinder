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


def find_best_station(path, stations, km_per_l, desired_litres, current_litres, has_rac, has_woolies):
    if len(path) < 2:
        return None

    # Bounding box pre-filter — keeps only stations within ~11 km of the route envelope
    lats = [p[0] for p in path]
    lons = [p[1] for p in path]
    lat_min, lat_max = min(lats) - 0.1, max(lats) + 0.1
    lon_min, lon_max = min(lons) - 0.1, max(lons) + 0.1
    nearby = [
        s for s in stations
        if lat_min <= s["lat"] <= lat_max and lon_min <= s["lon"] <= lon_max
    ]

    dest_lat, dest_lon = path[-1][0], path[-1][1]
    best = None
    best_cost = float("inf")

    for s in nearby:
        price = s["price"]
        if has_rac    and s["brand"] in RAC_BRANDS:    price -= 4
        if has_woolies and s["brand"] in WOOLIES_BRANDS: price -= 4

        # Find the route segment where this station causes minimum diversion
        min_div   = float("inf")
        best_d_to = None
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            seg   = haversine(a[0], a[1], b[0], b[1])
            d_to  = haversine(a[0], a[1], s["lat"], s["lon"])
            d_from = haversine(s["lat"], s["lon"], b[0], b[1])
            div = d_to + d_from - seg
            if div < min_div:
                min_div   = div
                best_d_to = d_to

        if min_div > 5:
            continue  # more than 5 km out of the way

        if best_d_to > current_litres * km_per_l:
            continue  # can't reach it on current tank

        tank_at_servo  = current_litres - best_d_to / km_per_l
        litres_to_buy  = max(0.0, desired_litres - tank_at_servo)
        d_to_dest      = haversine(s["lat"], s["lon"], dest_lat, dest_lon)
        total_cost     = litres_to_buy * price + (d_to_dest / km_per_l) * price

        if total_cost < best_cost:
            best_cost = total_cost
            best = {
                "address":         s["address"],
                "brand":           s["brand"],
                "price":           s["price"],
                "price_effective": price,
                "lat":             s["lat"],
                "lon":             s["lon"],
                "diversion_km":    round(min_div, 2),
                "total_cost_cents": round(total_cost, 1),
            }

    return best


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
            km_per_l        = float(body["efficiency"])
            desired_litres  = float(body["capacity"])
            current_litres  = float(body["current_tank"])
            # Frontend sends 0 when checkbox is checked (has card), 1 when unchecked
            has_rac    = int(body.get("RAC",    1)) == 0
            has_woolies = int(body.get("Woolies", 1)) == 0

            stations = fetch_stations()
            result   = find_best_station(path, stations, km_per_l, desired_litres, current_litres, has_rac, has_woolies)

            if result is None:
                self._json(404, {"error": "No suitable station found along this route"})
                return

            # Same response shape as the original Django view: [address, diversion, cost, [lat, lon]]
            payload = [result["address"], result["diversion_km"], result["total_cost_cents"], [result["lat"], result["lon"]]]
            self._json(200, payload)

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
