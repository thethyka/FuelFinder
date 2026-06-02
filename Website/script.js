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
let routePoints = [];
let stationMarker = null;

direction.on('route', (event) => {
  const pointsArr = [];
  const allSteps  = event.route[0].legs[0].steps;
  for (let i = 0; i < allSteps.length; i++) {
    const intersections = allSteps[i].intersections;
    for (let j = 0; j < intersections.length; j++) {
      pointsArr.push([intersections[j].location[1], intersections[j].location[0]]);
    }
  }
  routePoints = pointsArr;
  calculateButton.textContent = 'Calculate';
  calculateButton.disabled = false;
});

calculateButton.addEventListener('click', () => {
  if (!routePoints.length) {
    calculateButton.textContent = 'Pick route first';
    setTimeout(() => { calculateButton.textContent = 'Calculate'; }, 1400);
    return;
  }

  calculateButton.textContent = 'Calculating...';
  calculateButton.disabled = true;

  const resetCalculateButton = () => {
    calculateButton.textContent = 'Calculate';
    calculateButton.disabled = false;
  };

  const efficiency  = parseFloat(document.getElementById('fuel-efficiency').value)  || 12;
  const capacity    = parseFloat(document.getElementById('tank-after-fill').value)   || 50;
  const currentTank = parseFloat(document.getElementById('current-tank').value)      || 5;
  const rac         = document.getElementById('rac-member').checked ? 0 : 1;
  const woolies     = document.getElementById('woolworths-rewards-program').checked ? 0 : 1;

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/servo', true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify({
    path: routePoints,
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
        const latitude  = data[3][0];
        const longitude = data[3][1];

        // Remove previous marker if any
        if (stationMarker) stationMarker.remove();

        direction.addWaypoint(1, [longitude, latitude]);

        stationMarker = new mapboxgl.Marker({ color: '#88eeff' })
          .setLngLat([longitude, latitude])
          .setPopup(new mapboxgl.Popup().setHTML(
            `<strong style="color:#111">${data[0]}</strong><br>` +
            `<span style="color:#333">${data[2] / 100 < 100 ? '$' + (data[2] / 100).toFixed(2) : Math.round(data[2] / 100) + '¢'} estimated · ${data[1]} km diversion</span>`
          ))
          .addTo(map);
        stationMarker.getPopup().addTo(map);
      } else {
        console.error('No station found:', data);
      }
    } catch (error) {
      console.error('Could not process servo response', error);
    } finally {
      resetCalculateButton();
    }
  };

  xhr.onerror = function () {
    resetCalculateButton();
  };
});

resetButton.addEventListener('click', () => {
  direction.removeWaypoint(1);
  if (stationMarker) { stationMarker.remove(); stationMarker = null; }
  routePoints = [];
  calculateButton.textContent = 'Calculate';
  calculateButton.disabled = false;
});
