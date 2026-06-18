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


# -- multi-stop ---------------------------------------------------------------

# At latitude -37°, 1° longitude ≈ 88.9 km.
# This path spans ~1000 km east-west at constant latitude.
LONG_ROUTE = [[-37.0, 144.0 + i] for i in range(12)] + [[-37.0, 155.25]]


def test_multi_stop_two_stations_on_long_route():
    """1000 km trip, 50L tank at 10L/100km = 470 km usable range → needs 2 stops."""
    stations = [
        make_station(-37.0, 147.37, 170.0, brand="StopA"),  # ~300 km
        make_station(-37.0, 151.87, 165.0, brand="StopB"),  # ~700 km
    ]
    result = find_best_station(
        LONG_ROUTE, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=50.0,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "multi_stop"
    assert len(result["stations"]) == 2
    assert result["stations"][0]["brand"] == "StopA"
    assert result["stations"][1]["brand"] == "StopB"


def test_multi_stop_fuel_math_across_legs():
    """Litres-to-buy reflects fuel burned on each leg, not just the first."""
    stations = [
        make_station(-37.0, 147.37, 170.0, brand="StopA"),  # ~300 km
        make_station(-37.0, 151.87, 170.0, brand="StopB"),  # ~700 km
    ]
    result = find_best_station(
        LONG_ROUTE, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=50.0,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "multi_stop"
    s0, s1 = result["stations"]
    # Leg 1: 50L start, ~300 km at 10L/100km burns ~30L, arrive ~20L, buy ~30L
    assert 25 < s0["litres_to_buy"] < 35
    # Leg 2: 50L after fill, ~400 km burns ~40L, arrive ~10L, buy ~40L
    assert 35 < s1["litres_to_buy"] < 45


def test_multi_stop_back_half_heuristic():
    """A cheaper station in the front half is skipped for one in the back half."""
    stations = [
        make_station(-37.0, 145.5, 140.0, brand="CheapEarly"),  # ~133 km (front half)
        make_station(-37.0, 147.37, 170.0, brand="BackHalf"),    # ~300 km (back half)
        make_station(-37.0, 151.87, 170.0, brand="StopB"),       # ~700 km
    ]
    result = find_best_station(
        LONG_ROUTE, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=50.0,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "multi_stop"
    assert result["stations"][0]["brand"] == "BackHalf"


def test_multi_stop_cheapest_in_back_half():
    """Among stations in the back half, the cheapest wins."""
    stations = [
        make_station(-37.0, 147.37, 190.0, brand="Expensive"),  # ~300 km
        make_station(-37.0, 147.80, 150.0, brand="Cheap"),      # ~338 km (also back half)
        make_station(-37.0, 151.87, 170.0, brand="StopB"),      # ~700 km
    ]
    result = find_best_station(
        LONG_ROUTE, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=50.0,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "multi_stop"
    assert result["stations"][0]["brand"] == "Cheap"


def test_multi_stop_gap_in_coverage_returns_too_far():
    """If no station exists in a critical window, multi-stop fails to too_far."""
    # Only one station at ~300 km — nothing reachable for the second leg
    stations = [
        make_station(-37.0, 147.37, 170.0, brand="OnlyStop"),
    ]
    result = find_best_station(
        LONG_ROUTE, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=50.0,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "too_far"


def test_multi_stop_hard_cap_at_five():
    """A route needing 6 stops exceeds the cap and returns too_far."""
    # ~3500 km route. 50L at 10L/100km = 470 km usable per fill → needs ~7 stops.
    huge_route = [[-37.0, 110.0 + i] for i in range(40)] + [[-37.0, 149.5]]
    # Place a station every ~400 km (back-half sweet spot)
    stations = [
        make_station(-37.0, 113.5, 170.0, brand="S1"),
        make_station(-37.0, 118.0, 170.0, brand="S2"),
        make_station(-37.0, 122.5, 170.0, brand="S3"),
        make_station(-37.0, 127.0, 170.0, brand="S4"),
        make_station(-37.0, 131.5, 170.0, brand="S5"),
        make_station(-37.0, 136.0, 170.0, brand="S6"),
        make_station(-37.0, 140.5, 170.0, brand="S7"),
        make_station(-37.0, 145.0, 170.0, brand="S8"),
    ]
    result = find_best_station(
        huge_route, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=50.0,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "too_far"


def test_multi_stop_detour_cap_respected():
    """Stations beyond MAX_DETOUR_KM are skipped even in multi-stop."""
    stations = [
        make_station(-37.0, 147.37, 170.0, brand="OnRoute"),     # ~300 km, on route
        make_station(-37.1, 151.87, 150.0, brand="FarOff"),      # ~700 km, ~11 km off route
    ]
    result = find_best_station(
        LONG_ROUTE, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=50.0,
        has_rac=False,
        has_woolies=False,
    )
    # FarOff is too far from the route — chain can't be completed
    assert result["status"] == "too_far"


def test_single_stop_still_returns_ok():
    """A trip needing one stop uses the single-stop path, not multi-stop."""
    # ~400 km route, 50L tank at 10L/100km, start with 10L → needs a stop
    # but one fill (50L) covers remaining ~300 km easily
    medium_route = [[-37.0, 144.0 + i] for i in range(5)] + [[-37.0, 148.5]]
    stations = [
        make_station(-37.0, 145.5, 170.0, brand="MidRoute"),
    ]
    result = find_best_station(
        medium_route, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=20.0,
        has_rac=False,
        has_woolies=False,
    )
    assert result["status"] == "ok"
    assert result["station"]["brand"] == "MidRoute"
