#!/usr/bin/env python3
"""Local dev server: serves Website/ as static files + /api/servo endpoint."""
import sys, os, json
from http.server import HTTPServer, SimpleHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api'))
from servo import fetch_stations, find_best_station

WEBSITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Website')

class DevHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEBSITE_DIR, **kwargs)

    def do_POST(self):
        if self.path != '/api/servo':
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))

            path          = body['path']
            km_per_l      = float(body['efficiency'])
            desired       = float(body['capacity'])
            current       = float(body['current_tank'])
            has_rac       = int(body.get('RAC',    1)) == 0
            has_woolies   = int(body.get('Woolies', 1)) == 0

            print(f'  → {len(path)} route points | efficiency={km_per_l} | tank={current}/{desired}L | RAC={has_rac} | Woolies={has_woolies}')
            stations = fetch_stations()
            print(f'  → {len(stations)} stations fetched from FuelWatch')
            result = find_best_station(path, stations, km_per_l, desired, current, has_rac, has_woolies)

            if result is None:
                print('  → No station found')
                self._json(404, {'error': 'No suitable station found along this route'})
                return

            print(f'  → Best: {result["brand"]} @ {result["address"]} | {result["price"]} c/L | diversion {result["diversion_km"]} km')
            payload = [result['address'], result['diversion_km'], result['total_cost_cents'], [result['lat'], result['lon']]]
            self._json(200, payload)

        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {'error': str(e)})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f'  {args[0]} {args[1]}')

if __name__ == '__main__':
    port = 3000
    print(f'Dev server running → http://localhost:{port}')
    HTTPServer(('', port), DevHandler).serve_forever()
