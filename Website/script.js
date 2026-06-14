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
  if (navigator.permissions && navigator.permissions.query) {
    navigator.permissions
      .query({ name: 'geolocation' })
      .then((status) => {
        if (status.state === 'granted') centerOnUser();
      })
      .catch(() => {}); // Permissions API unsupported, stay on default view
  }
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
//  - hasFuelStop     : whether our single fuel waypoint (index 0) is present.
//  - pendingFuelStop : [lon, lat] queued to add once a removeWaypoint settles,
//                      so the two async route requests never race.
//  - stationMarker   : the pin for the chosen station.
// ---------------------------------------------------------------------------
let userRoutePoints = [];
let hasFuelStop     = false;
let pendingFuelStop = null;
let stationMarker   = null;

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

function clearMarker() {
  if (stationMarker) { stationMarker.remove(); stationMarker = null; }
}

// Remove our fuel stop + marker and return to the plain A→B route.
function clearFuelStop() {
  pendingFuelStop = null;
  clearMarker();
  if (hasFuelStop) {
    hasFuelStop = false;
    direction.removeWaypoint(0); // route event recaptures the base route
  }
}

direction.on('route', (event) => {
  // A removeWaypoint just settled and a replacement stop is queued; add it now,
  // after the route is back to A→B, so requests are strictly sequenced.
  if (pendingFuelStop) {
    const [lon, lat] = pendingFuelStop;
    pendingFuelStop  = null;
    hasFuelStop      = true;
    direction.addWaypoint(0, [lon, lat]);
    return; // button stays disabled until addWaypoint settles (next route event)
  }

  // Only a pure origin→destination route updates the base geometry.
  if (!hasFuelStop) {
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

// Place (or replace) the single fuel waypoint at index 0 without racing.
function setFuelStop(lon, lat) {
  if (hasFuelStop) {
    // Remove the old one first; the queued add fires on the next route event.
    hasFuelStop     = false;
    pendingFuelStop = [lon, lat];
    direction.removeWaypoint(0);
  } else {
    hasFuelStop = true;
    direction.addWaypoint(0, [lon, lat]);
  }
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

    // ROUTING_SPEC.md §5: switch on the status.
    switch (data && data.status) {
      case 'ok': {
        const st          = data.station;
        const costDollars = (st.cost_cents / 100).toFixed(2);
        clearMarker();
        stationMarker = new mapboxgl.Marker({ color: '#88eeff' })
          .setLngLat([st.lon, st.lat])
          .setPopup(new mapboxgl.Popup({ offset: 25 }).setHTML(
            `<strong style="font-size:13px;color:#111">${st.brand}, ${st.address}</strong><br>` +
            `<span style="color:#444;font-size:12px">~$${costDollars} est. &nbsp;·&nbsp; ${st.diversion_km} km detour</span>`
          ))
          .addTo(map);
        stationMarker.getPopup().addTo(map);
        // Button re-enables once the waypoint settles (next route event).
        setFuelStop(st.lon, st.lat);
        break;
      }

      case 'no_stop_needed':
        clearFuelStop();
        flash(calculateButton, `No stop needed, arrive ~${data.tank_at_dest} L`);
        break;

      case 'too_far':
        clearFuelStop();
        flash(calculateButton, 'Too far for one tank');
        break;

      case 'unreachable':
        clearFuelStop();
        flash(calculateButton, 'No station near route');
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

function flash(btn, msg) {
  const prev    = btn.textContent;
  btn.textContent = msg;
  btn.disabled    = false;
  setTimeout(() => {
    btn.textContent = prev;
    btn.disabled    = false;
  }, 2000);
}
