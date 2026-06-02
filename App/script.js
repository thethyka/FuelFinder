document.getElementById('help-modal').style.display = 'flex';

mapboxgl.accessToken = 'pk.eyJ1Ijoic21vaGFuMjAwMSIsImEiOiJjbDVkaDZiNmwwNmwwM2ZvMXYwbDVieG9uIn0.hpEwFPqANepe3lUep9EU1Q';
const map = new mapboxgl.Map({
    container: 'map', // container ID
    style: 'mapbox://styles/smohan2001/cl5dh88vv000o15pi194xhvy2', // style URL
    center: [115.816,-31.980], // starting position [lng, lat]
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

let pointsArr = []

direction.on('route', (event) => {
  const tempArr = []
  console.log(event);
  const allSteps = event.route[0].legs[0].steps
  const stepsLen = event.route[0].legs[0].steps.length

  for (let i = 0; i < stepsLen; i++) {
    intersectionLen = allSteps[i].intersections.length
    intersections = allSteps[i].intersections
    for (let j = 0; j < intersectionLen; j++){
      tempArr.push([intersections[j].location[1],intersections[j].location[0]])
    }
  }
  pointsArr = tempArr
 console.log(pointsArr)
});

function getWaypoint(){
  document.getElementById('car').removeAttribute('class')
  var RACdisct = document.querySelector('#rac-member').checked ? 0 : 1
  var Wooliesdisct = document.querySelector('#woolworths-rewards-program').checked ? 0 : 1
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "http://localhost:8000/servo/", true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify({
    path: pointsArr,
    efficiency: document.querySelector('#fuel-effiency').value,
    capacity: document.querySelector('#tank-after-fill').value,
    current_tank: document.querySelector('#current-tank').value,
    RAC: RACdisct,
    Woolies: Wooliesdisct,
  }));
  xhr.onload = function () {
    console.log("HELLO")
    console.log(this.responseText);
    var data = JSON.parse(this.responseText);
    console.log(data);
    var latitude = data[3][0]
    var longitude = data[3][1]
  direction.addWaypoint (
    1, [longitude,latitude])
  
  const marker = new mapboxgl.Marker()
  .setLngLat([longitude,latitude])
  .addTo(map);
  
  setTimeout(() => {
    document.getElementById('car').setAttribute('class', 'hidden')
  }, 4000)
}
}

function removeWaypoint() {
  direction.removeWaypoint (0)
};

map.addControl(
  direction,
);

