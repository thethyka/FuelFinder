mapboxgl.accessToken = window.MAPBOX_ACCESS_TOKEN;

if (!mapboxgl.accessToken) {
  throw new Error('Missing Mapbox token. Set MAPBOX_TOKEN in GitHub Actions secrets.');
}

const map = new mapboxgl.Map({
  container: 'map', // container ID
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
        paint: {
          'background-color': '#080617'
        }
      },
      {
        id: 'land',
        type: 'fill',
        source: 'mapbox-streets',
        'source-layer': 'landuse',
        paint: {
          'fill-color': '#121126',
          'fill-opacity': 0.9
        }
      },
      {
        id: 'water',
        type: 'fill',
        source: 'mapbox-streets',
        'source-layer': 'water',
        paint: {
          'fill-color': '#050b1f'
        }
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
  center: [115.816, -31.980], // starting position [lng, lat]
  zoom: 13 // starting zoom
});

map.on('style.load', () => {
  map.setFog({}); // Set the default atmosphere style
});

map.addControl(
  new mapboxgl.GeolocateControl({
    positionOptions: {
      enableHighAccuracy: true
    },
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

const calculateButton = document.getElementById('btn');
let routePoints = [];

direction.on('route', (event) => {
  const pointsArr = [];
  const allSteps = event.route[0].legs[0].steps;
  const stepsLen = event.route[0].legs[0].steps.length;

  for (let i = 0; i < stepsLen; i++) {
    const intersectionLen = allSteps[i].intersections.length;
    const intersections = allSteps[i].intersections;
    for (let j = 0; j < intersectionLen; j++) {
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
    setTimeout(() => {
      calculateButton.textContent = 'Calculate';
    }, 1400);
    return;
  }

  calculateButton.textContent = 'Calculating...';
  calculateButton.disabled = true;

  const resetCalculateButton = () => {
    calculateButton.textContent = 'Calculate';
    calculateButton.disabled = false;
  };

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/servo", true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify({
    path: routePoints,
    efficiency: 5,
    capacity: 50,
    current_tank: 2,
    RAC: 0,
    Woolies: 0
  }));

  xhr.onload = function () {
    try {
      console.log("HELLO")
      console.log(this.responseText);
      var data = JSON.parse(this.responseText);
      console.log(data);
    } catch (error) {
      console.error('Could not process servo response', error);
    } finally {
      resetCalculateButton();
    }
  }

  xhr.onerror = function () {
    resetCalculateButton();
  }
});

map.addControl(
  direction,
  'top-right'
);

/* direction.addWaypoint (
  1, [lng, lat]
); */
