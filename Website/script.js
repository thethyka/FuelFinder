mapboxgl.accessToken = window.MAPBOX_ACCESS_TOKEN;

if (!mapboxgl.accessToken) {
  throw new Error('Missing Mapbox token. Set MAPBOX_TOKEN in GitHub Actions secrets or Vercel env vars.');
}

const map = new mapboxgl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      'mapbox-streets': {
        type: 'vector',
        url: 'mapbox://mapbox.mapbox-streets-v8'
      }
    },
    glyphs: 'mapbox://fonts/mapbox/{fontstack}/{range}.pbf',
    layers: [
      {
        id: 'background',
        type: 'background',
        paint: { 'background-color': '#080617' }
      },
      {
        id: 'land',
        type: 'fill',
        source: 'mapbox-streets',
        'source-layer': 'landuse',
        paint: { 'fill-color': '#121126', 'fill-opacity': 0.9 }
      },
      {
        id: 'water',
        type: 'fill',
        source: 'mapbox-streets',
        'source-layer': 'water',
        paint: { 'fill-color': '#050b1f' }
      },
      {
        id: 'roads-minor',
        type: 'line',
        source: 'mapbox-streets',
        'source-layer': 'road',
        filter: ['match', ['get', 'class'], ['street', 'street_limited', 'service', 'track'], true, false],
        paint: {
          'line-color': '#26365c',
          'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.5, 16, 2],
          'line-opacity': 0.55
        }
      },
      {
        id: 'roads-major-glow',
        type: 'line',
        source: 'mapbox-streets',
        'source-layer': 'road',
        filter: ['match', ['get', 'class'], ['motorway', 'trunk', 'primary', 'secondary'], true, false],
        paint: {
          'line-color': '#7a5cff',
          'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1, 16, 5],
          'line-blur': 2.5,
          'line-opacity': 0.28
        }
      },
      {
        id: 'roads-major',
        type: 'line',
        source: 'mapbox-streets',
        'source-layer': 'road',
        filter: ['match', ['get', 'class'], ['motorway', 'trunk', 'primary', 'secondary'], true, false],
        paint: {
          'line-color': '#b08cff',
          'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.7, 16, 2.4],
          'line-opacity': 0.78
        }
      },
      {
        id: 'road-labels',
        type: 'symbol',
        source: 'mapbox-streets',
        'source-layer': 'road',
        minzoom: 12,
        layout: {
          'symbol-placement': 'line',
          'text-field': ['get', 'name'],
          'text-font': ['Open Sans Regular'],
          'text-size': 11
        },
        paint: {
          'text-color': '#9be7dc',
          'text-halo-color': '#080617',
          'text-halo-width': 1.5
        }
      },
      {
        id: 'place-labels',
        type: 'symbol',
        source: 'mapbox-streets',
        'source-layer': 'place_label',
        layout: {
          'text-field': ['get', 'name'],
          'text-font': ['Open Sans Semibold'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 4, 11, 12, 16]
        },
        paint: {
          'text-color': '#d8c982',
          'text-halo-color': '#080617',
          'text-halo-width': 2
        }
      }
    ]
  },
  center: [115.816, -31.980],
  zoom: 13
});

map.on('style.load', () => {
  map.setFog({});
});

// On load, recenter on the user's location *only if they've already shared it*
// (permission already granted), otherwise we stay on the default view and never
// prompt unsolicited. The GeolocateControl below still lets them opt in manually.
function centerOnUser() {
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { longitude, latitude } = pos.coords;
      map.jumpTo({ center: [longitude, latitude], zoom: 13 });
    },
    () => {}, // denied or unavailable, keep the default view
    { enableHighAccuracy: true, maximumAge: 60000, timeout: 8000 }
  );
}

if (navigator.geolocation) {
  centerOnUser();
}

map.addControl(
  new mapboxgl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true },
    trackUserLocation: true,
    showUserHeading: true,
  }),
  'bottom-right'
);

const direction = new MapboxDirections({
  accessToken: mapboxgl.accessToken,
  unit: 'metric',
  profile: 'mapbox/driving',
});

map.addControl(direction, 'top-right');

// Show modal on load
document.getElementById('help-modal').style.display = 'flex';

const calculateButton = document.getElementById('btn');
const resetButton     = document.getElementById('btn2');

// ---------------------------------------------------------------------------
// Route + fuel-stop state. See ROUTING_SPEC.md §7 for the rules this enforces.
//
//  - userRoutePoints : the pure origin→destination geometry. Updated ONLY from
//                      route events fired while no fuel waypoint of ours exists.
//                      This is the only thing ever sent to the API.
//  - fuelStopCount   : how many of our fuel waypoints are currently in the
//                      route, occupying indices 0..count-1 in route order.
//  - opQueue         : queued waypoint operations (add/remove). The Directions
//                      plugin fires one async route request per waypoint change,
//                      so we drain exactly one op per settled `route` event —
//                      the requests are strictly sequenced and never race. This
//                      generalises the old single-slot replace logic to N stops.
//  - stationMarkers  : the pins for the chosen station(s).
// ---------------------------------------------------------------------------
let userRoutePoints = [];
let fuelStopCount   = 0;
let opQueue         = [];
let stationMarkers  = [];

function extractPoints(event) {
  const pts = [];
  for (const leg of event.route[0].legs) {
    for (const step of leg.steps) {
      for (const ix of step.intersections) {
        pts.push([ix.location[1], ix.location[0]]); // [lat, lon]
      }
    }
  }
  return pts;
}

function clearMarkers() {
  for (const m of stationMarkers) m.remove();
  stationMarkers = [];
}

// Execute one queued waypoint op. Each op fires an async route request; the
// `route` handler calls pump() again to run the next, so they never overlap.
function pump() {
  if (opQueue.length) opQueue.shift()();
}

// Replace whatever fuel waypoints are currently present with `coordsList`
// ([lon, lat] in route order). Removals drain first, then the new adds, one per
// settled route event. An empty list just clears all our waypoints.
function applyFuelStops(coordsList) {
  opQueue = [];
  for (let i = 0; i < fuelStopCount; i++) {
    opQueue.push(() => { direction.removeWaypoint(0); fuelStopCount--; });
  }
  for (const [lon, lat] of coordsList) {
    opQueue.push(() => { direction.addWaypoint(fuelStopCount, [lon, lat]); fuelStopCount++; });
  }
  pump();
}

// Remove our fuel stops + markers and return to the plain A→B route.
function clearFuelStop() {
  clearMarkers();
  applyFuelStops([]);
}

direction.on('route', (event) => {
  // A waypoint op just settled; run the next queued one so the two async route
  // requests never race. Button stays disabled until the queue drains.
  if (opQueue.length) {
    pump();
    return;
  }

  // Only a pure origin→destination route updates the base geometry.
  if (fuelStopCount === 0) {
    userRoutePoints = extractPoints(event);
  }

  calculateButton.textContent = 'Calculate';
  calculateButton.disabled    = false;
});

// If the user edits either endpoint, any existing stop is stale, so drop it.
// The next route they draw is captured fresh, so old locations never linger.
//
// BUT the Directions plugin re-fires 'origin'/'destination' as a side effect of
// addWaypoint/removeWaypoint, with the endpoints unchanged. Acting on those would
// instantly tear down the stop we just placed (the pin "flashes" and vanishes).
// So we only invalidate when an endpoint's coordinates have actually changed.
let lastOriginKey = null;
let lastDestKey   = null;

function coordKey(feature) {
  return feature && feature.geometry ? feature.geometry.coordinates.join(',') : null;
}

function onEndpointChange() {
  const oKey = coordKey(direction.getOrigin());
  const dKey = coordKey(direction.getDestination());
  if (oKey === lastOriginKey && dKey === lastDestKey) return; // spurious re-fire
  lastOriginKey = oKey;
  lastDestKey   = dKey;
  clearFuelStop();
}

direction.on('origin',      onEndpointChange);
direction.on('destination', onEndpointChange);

// Drop a station pin with an open popup. `label` prefixes the title for
// multi-stop chains (e.g. "Stop 1") and is omitted for a single stop.
function placeStationMarker(st, costStr, label) {
  const title = label ? `${label}: ${st.brand}, ${st.address}` : `${st.brand}, ${st.address}`;
  const marker = new mapboxgl.Marker({ color: '#88eeff' })
    .setLngLat([st.lon, st.lat])
    .setPopup(new mapboxgl.Popup({ offset: 25 }).setHTML(
      `<strong style="font-size:13px;color:#111">${title}</strong><br>` +
      `<span style="color:#444;font-size:12px">${costStr} est. &nbsp;·&nbsp; ${st.diversion_km} km detour</span>`
    ))
    .addTo(map);
  marker.getPopup().addTo(map);
  stationMarkers.push(marker);
}

calculateButton.addEventListener('click', () => {
  if (!userRoutePoints.length) {
    flash(calculateButton, 'Draw a route first');
    return;
  }

  const efficiency  = parseFloat(document.getElementById('fuel-efficiency').value);
  const capacity    = parseFloat(document.getElementById('tank-capacity').value);
  const currentTank = parseFloat(document.getElementById('current-tank').value);

  if (isNaN(efficiency) || efficiency <= 0) {
    flash(calculateButton, 'Enter fuel efficiency');
    document.getElementById('fuel-efficiency').focus();
    return;
  }
  if (isNaN(capacity) || capacity <= 0) {
    flash(calculateButton, 'Enter tank capacity');
    document.getElementById('tank-capacity').focus();
    return;
  }
  if (isNaN(currentTank) || currentTank < 0) {
    flash(calculateButton, 'Enter current tank');
    document.getElementById('current-tank').focus();
    return;
  }

  calculateButton.textContent = 'Calculating...';
  calculateButton.disabled    = true;

  const rac     = document.getElementById('rac-member').checked ? 0 : 1;
  const woolies = document.getElementById('woolworths-rewards-program').checked ? 0 : 1;
  const fuel    = document.getElementById('fuel-type').value;

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/servo', true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify({
    path:         userRoutePoints,  // always the original A→B route
    efficiency:   efficiency,       // L/100km, backend converts to km/L
    capacity:     capacity,
    current_tank: currentTank,
    RAC:          rac,
    Woolies:      woolies,
    fuel:         fuel,             // 91 | 95 | 98 | DIESEL
  }));

  xhr.onload = function () {
    let data;
    try {
      data = JSON.parse(this.responseText);
    } catch (err) {
      flash(calculateButton, 'Error, try again');
      return;
    }

    updateDiscountUI(data && data.region);

    switch (data && data.status) {
      case 'ok': {
        const st      = data.station;
        const costMin = (st.cost_cents_min / 100).toFixed(2);
        const costMax = (st.cost_cents_max / 100).toFixed(2);
        const costStr = costMin === costMax ? `~$${costMin}` : `~$${costMin} – $${costMax}`;
        clearMarkers();
        placeStationMarker(st, costStr);
        // Button re-enables once the waypoint settles (next route event).
        applyFuelStops([[st.lon, st.lat]]);
        break;
      }

      case 'multi_stop': {
        const stops = data.stations || [];
        clearMarkers();
        stops.forEach((st, i) => {
          const costStr = `~$${(st.cost_cents / 100).toFixed(2)}`;
          placeStationMarker(st, costStr, `Stop ${i + 1}`);
        });
        // Button re-enables once the last waypoint settles (final route event).
        applyFuelStops(stops.map((st) => [st.lon, st.lat]));
        break;
      }

      case 'no_stop_needed':
        clearFuelStop();
        flash(calculateButton, `No stop needed, arrive ~${data.tank_at_dest} L`);
        break;

      case 'too_far':
        clearFuelStop();
        flash(calculateButton, 'Too far, even with multiple stops');
        break;

      case 'unreachable':
        clearFuelStop();
        flash(calculateButton, 'No station near route');
        break;

      case 'out_of_coverage':
        clearFuelStop();
        flash(calculateButton, 'Only WA & VIC routes supported');
        break;

      default:
        flash(calculateButton, 'Error, try again');
    }
  };

  xhr.onerror = function () {
    flash(calculateButton, 'Network error');
  };
});

resetButton.addEventListener('click', clearFuelStop);

function updateDiscountUI(region) {
  const racRow    = document.getElementById('rac-row');
  const racCheck  = document.getElementById('rac-member');
  const racLabel  = document.getElementById('rac-label');
  const woolRow   = document.getElementById('woolies-row');
  const woolCheck = document.getElementById('woolworths-rewards-program');

  if (!region) {
    racRow.style.display  = 'none';
    woolRow.style.display = 'none';
    racCheck.checked  = false;
    woolCheck.checked = false;
    return;
  }

  const discounts = {
    WA:  { rac: true, racLabel: 'RAC member',  woolies: true },
    VIC: { rac: true, racLabel: 'RACV member', woolies: true },
  };
  const d = discounts[region] || {};

  racRow.style.display  = d.rac     ? '' : 'none';
  woolRow.style.display = d.woolies ? '' : 'none';
  racLabel.textContent  = d.racLabel || 'RAC/RACV member';

  if (!d.rac)     racCheck.checked  = false;
  if (!d.woolies) woolCheck.checked = false;
}

function flash(btn, msg) {
  const prev    = btn.textContent;
  btn.textContent = msg;
  btn.disabled    = false;
  setTimeout(() => {
    btn.textContent = prev;
    btn.disabled    = false;
  }, 2000);
}
