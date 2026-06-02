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
            efficiency    = float(body['efficiency'])   # L/100km
            capacity      = float(body['capacity'])     # max tank size
            current       = float(body['current_tank'])
            has_rac       = int(body.get('RAC',    1)) == 0
            has_woolies   = int(body.get('Woolies', 1)) == 0

            print(f'  → {len(path)} route points | efficiency={efficiency} L/100km | tank={current}/{capacity}L | RAC={has_rac} | Woolies={has_woolies}')
            stations = fetch_stations()
            print(f'  → {len(stations)} stations fetched from FuelWatch')
            result = find_best_station(path, stations, efficiency, capacity, current, has_rac, has_woolies)

            status = result.get('status')
            if status == 'ok':
                st = result['station']
                print(f'  → Best: {st["brand"]} @ {st["address"]} | {st["effective_price"]} c/L | diversion {st["diversion_km"]} km')
            else:
                print(f'  → {status}')
            self._json(200, result)

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
