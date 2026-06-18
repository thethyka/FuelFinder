"""Tests for the core routing logic in servo.py.

These test pure computation — no network calls, no external APIs.
"""

from api.servo import (
    haversine,
    route_bbox,
    cumulative_distances,
    find_best_station,
    RESERVE_L,
    MAX_DETOUR_KM,
)


# -- Helpers ------------------------------------------------------------------

MELBOURNE_CBD = [-37.8136, 144.9631]
FOOTSCRAY = [-37.8000, 144.9000]
SHORT_ROUTE = [FOOTSCRAY, [-37.8100, 144.9300], MELBOURNE_CBD]


def make_station(lat, lon, price, brand="TestBrand", address="123 Test St"):
    return {
        "lat": lat,
        "lon": lon,
        "price": price,
        "brand": brand,
        "address": address,
    }


# -- haversine ----------------------------------------------------------------


def test_haversine_zero_distance():
    assert haversine(-37.8, 144.9, -37.8, 144.9) == 0.0


def test_haversine_known_distance():
    km = haversine(FOOTSCRAY[0], FOOTSCRAY[1], MELBOURNE_CBD[0], MELBOURNE_CBD[1])
    assert 4.0 < km < 7.0


# -- route_bbox ---------------------------------------------------------------


def test_route_bbox_no_padding():
    bbox = route_bbox(SHORT_ROUTE, pad=0.0)
    lat_min, lat_max, lon_min, lon_max = bbox
    assert lat_min == min(p[0] for p in SHORT_ROUTE)
    assert lat_max == max(p[0] for p in SHORT_ROUTE)
    assert lon_min == min(p[1] for p in SHORT_ROUTE)
    assert lon_max == max(p[1] for p in SHORT_ROUTE)


def test_route_bbox_with_padding():
    bbox = route_bbox(SHORT_ROUTE, pad=0.1)
    bbox_tight = route_bbox(SHORT_ROUTE, pad=0.0)
    assert bbox[0] < bbox_tight[0]
    assert bbox[1] > bbox_tight[1]


# -- cumulative_distances -----------------------------------------------------


def test_cumulative_distances_starts_at_zero():
    cum = cumulative_distances(SHORT_ROUTE)
    assert cum[0] == 0.0
    assert len(cum) == len(SHORT_ROUTE)


def test_cumulative_distances_monotonic():
    cum = cumulative_distances(SHORT_ROUTE)
    for i in range(1, len(cum)):
        assert cum[i] > cum[i - 1]


# -- find_best_station --------------------------------------------------------


def test_no_stop_needed_full_tank():
    stations = [make_station(-37.81, 144.95, 180.0)]
    result = find_best_station(
        SHORT_ROUTE,
        stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=45.0,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "no_stop_needed"
    assert result["tank_at_dest"] > RESERVE_L


def test_stop_needed_low_tank():
    stations = [make_station(-37.805, 144.92, 180.0)]
    result = find_best_station(
        SHORT_ROUTE,
        stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=3.4,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "ok"
    assert result["station"]["price"] == 180.0


def test_picks_cheapest_station():
    stations = [
        make_station(-37.81, 144.95, 200.0, brand="Expensive"),
        make_station(-37.81, 144.94, 150.0, brand="Cheap"),
    ]
    result = find_best_station(
        SHORT_ROUTE, stations, 10.0, 50.0, 3.4, False, False
    )
    assert result["status"] == "ok"
    assert result["station"]["brand"] == "Cheap"


def test_unreachable_no_stations_nearby():
    stations = [make_station(-30.0, 130.0, 150.0)]
    result = find_best_station(
        SHORT_ROUTE, stations, 10.0, 50.0, 3.4, False, False
    )
    assert result["status"] == "unreachable"


def test_no_route_too_few_points():
    result = find_best_station(
        [MELBOURNE_CBD], [], 10.0, 50.0, 2.0, False, False
    )
    assert result["status"] == "no_route"
