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

let baseRoutePoints = [];   // original A→B points, never includes the station waypoint
let stationActive   = false;
let pendingWaypoint = null; // [lon, lat] queued to add once removeWaypoint settles
let stationMarker   = null;

direction.on('route', (event) => {
  // Collect points across all legs
  const pointsArr = [];
  for (const leg of event.route[0].legs) {
    for (const step of leg.steps) {
      for (const ix of step.intersections) {
        pointsArr.push([ix.location[1], ix.location[0]]);
      }
    }
  }

  if (pendingWaypoint) {
    // removeWaypoint just settled — route is back to A→B.
    // Save it, then add the new station waypoint.
    baseRoutePoints = pointsArr;
    const [lon, lat] = pendingWaypoint;
    pendingWaypoint  = null;
    stationActive    = true;
    direction.addWaypoint(1, [lon, lat]);
    return; // button stays disabled until addWaypoint settles (next route event)
  }

  if (!stationActive) {
    // Pure user-drawn A→B route — save as base
    baseRoutePoints = pointsArr;
  }
  // Re-enable after every settled route (initial draw, or after addWaypoint finishes)
  calculateButton.textContent = 'Calculate';
  calculateButton.disabled    = false;
});

calculateButton.addEventListener('click', () => {
  if (!baseRoutePoints.length) {
    flash(calculateButton, 'Draw a route first');
    return;
  }

  const efficiency  = parseFloat(document.getElementById('fuel-efficiency').value);
  const capacity    = parseFloat(document.getElementById('tank-after-fill').value);
  const currentTank = parseFloat(document.getElementById('current-tank').value);

  if (isNaN(efficiency) || efficiency <= 0) {
    flash(calculateButton, 'Enter fuel efficiency');
    document.getElementById('fuel-efficiency').focus();
    return;
  }
  if (isNaN(capacity) || capacity <= 0) {
    flash(calculateButton, 'Enter tank size');
    document.getElementById('tank-after-fill').focus();
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

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/servo', true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify({
    path:         baseRoutePoints,  // always send the original A→B route
    efficiency:   efficiency,
    capacity:     capacity,
    current_tank: currentTank,
    RAC:          rac,
    Woolies:      woolies,
  }));

  xhr.onload = function () {
    try {
      const data = JSON.parse(this.responseText);
      if (this.status === 200 && Array.isArray(data)) {
        const lat         = data[3][0];
        const lon         = data[3][1];
        const costDollars = (data[2] / 100).toFixed(2);

        // Update marker
        if (stationMarker) stationMarker.remove();
        stationMarker = new mapboxgl.Marker({ color: '#88eeff' })
          .setLngLat([lon, lat])
          .setPopup(new mapboxgl.Popup({ offset: 25 }).setHTML(
            `<strong style="font-size:13px;color:#111">${data[0]}</strong><br>` +
            `<span style="color:#444;font-size:12px">~$${costDollars} est. &nbsp;·&nbsp; ${data[1]} km detour</span>`
          ))
          .addTo(map);
        stationMarker.getPopup().addTo(map);

        // Update route waypoint.
        // Button stays disabled — route event re-enables it once the waypoint settles.
        if (stationActive) {
          // Station already in route: queue new one, remove old one first
          stationActive    = false;
          pendingWaypoint  = [lon, lat];
          direction.removeWaypoint(1);
        } else {
          stationActive = true;
          direction.addWaypoint(1, [lon, lat]);
        }
      } else {
        flash(calculateButton, 'No station found');
      }
    } catch (err) {
      flash(calculateButton, 'Error — try again');
    }
  };

  xhr.onerror = function () {
    flash(calculateButton, 'Network error');
  };
});

resetButton.addEventListener('click', () => {
  if (stationMarker) { stationMarker.remove(); stationMarker = null; }
  if (stationActive) {
    stationActive = false;
    direction.removeWaypoint(1);
    // route event will re-enable button and restore baseRoutePoints
  }
});

function flash(btn, msg) {
  const prev    = btn.textContent;
  btn.textContent = msg;
  btn.disabled    = false;
  setTimeout(() => { btn.textContent = prev; }, 2000);
}
