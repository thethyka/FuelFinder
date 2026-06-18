"""Tests for VIC API integration and multi-state support."""

from api.servo import (
    detect_state,
    parse_vic_stations,
    get_discount,
    fetch_stations_vic_cached,
    get_stations,
    find_best_station,
)


def test_melbourne_is_vic():
    assert detect_state(-37.81, 144.96) == "VIC"


def test_perth_is_wa():
    assert detect_state(-31.95, 115.86) == "WA"


def test_sydney_is_not_covered():
    assert detect_state(-33.87, 151.21) is None


# -- VIC station parsing ------------------------------------------------------

SAMPLE_BRANDS = {
    "brand_shell": "Shell",
    "brand_bp": "BP",
}

SAMPLE_VIC_RESPONSE = {
    "fuelPriceDetails": [
        {
            "fuelStation": {
                "id": "station1",
                "name": "Test Shell Clayton",
                "address": "123 Main St, CLAYTON, 3168",
                "brandId": "brand_shell",
                "contactPhone": "0399990000",
                "location": {"latitude": -37.93, "longitude": 145.13},
            },
            "fuelPrices": [
                {"fuelType": "U91", "isAvailable": True, "price": 163.9, "updatedAt": "2026-06-17T05:50:21Z"},
                {"fuelType": "P95", "isAvailable": True, "price": 177.9, "updatedAt": "2026-06-17T05:50:21Z"},
                {"fuelType": "DSL", "isAvailable": False, "price": None, "updatedAt": "2026-06-17T05:50:21Z"},
            ],
            "updatedAt": "2026-06-17T05:50:21Z",
        },
        {
            "fuelStation": {
                "id": "station2",
                "name": "No Location Station",
                "address": "456 Nowhere Rd",
                "brandId": "brand_bp",
                "location": {"latitude": None, "longitude": None},
            },
            "fuelPrices": [
                {"fuelType": "U91", "isAvailable": True, "price": 170.0, "updatedAt": "2026-06-17T05:50:21Z"},
            ],
            "updatedAt": "2026-06-17T05:50:21Z",
        },
    ]
}


def test_parse_vic_stations_basic_fields():
    stations = parse_vic_stations(SAMPLE_VIC_RESPONSE, SAMPLE_BRANDS, fuel="91")
    assert len(stations) == 1
    s = stations[0]
    assert s["lat"] == -37.93
    assert s["lon"] == 145.13
    assert s["price"] == 163.9
    assert s["brand"] == "Shell"
    assert "CLAYTON" in s["address"]


def test_parse_vic_stations_filters_unavailable():
    stations = parse_vic_stations(SAMPLE_VIC_RESPONSE, SAMPLE_BRANDS, fuel="DIESEL")
    assert len(stations) == 0


def test_parse_vic_stations_filters_null_location():
    stations = parse_vic_stations(SAMPLE_VIC_RESPONSE, SAMPLE_BRANDS, fuel="91")
    assert all(s["lat"] is not None for s in stations)


def test_parse_vic_stations_fuel_type_selection():
    stations = parse_vic_stations(SAMPLE_VIC_RESPONSE, SAMPLE_BRANDS, fuel="95")
    assert len(stations) == 1
    assert stations[0]["price"] == 177.9


# -- Region-aware discounts ---------------------------------------------------


def test_vic_racv_discount_eg_ampol():
    assert get_discount("VIC", "EG Ampol", has_auto_club=True, has_woolies=False) == 5


def test_vic_racv_no_discount_bp():
    assert get_discount("VIC", "BP", has_auto_club=True, has_woolies=False) == 0


def test_vic_woolies_discount_ampol():
    assert get_discount("VIC", "Ampol", has_auto_club=False, has_woolies=True) == 4


def test_vic_best_of_racv_and_woolies():
    assert get_discount("VIC", "EG Ampol", has_auto_club=True, has_woolies=True) == 5


def test_wa_rac_discount_puma():
    assert get_discount("WA", "Puma", has_auto_club=True, has_woolies=False) == 4


def test_wa_rac_no_discount_shell():
    assert get_discount("WA", "Shell", has_auto_club=True, has_woolies=False) == 0


def test_wa_woolies_discount_caltex():
    assert get_discount("WA", "Caltex", has_auto_club=False, has_woolies=True) == 4


def test_wa_best_of_rac_and_woolies():
    assert get_discount("WA", "Caltex", has_auto_club=True, has_woolies=True) == 4


def test_no_discounts_when_neither_card():
    assert get_discount("VIC", "EG Ampol", has_auto_club=False, has_woolies=False) == 0


# -- VIC end-to-end route tests -----------------------------------------------

MELBOURNE_ROUTE = [
    [-37.8000, 144.9000],  # Footscray
    [-37.8100, 144.9300],
    [-37.8136, 144.9631],  # CBD
]

REALISTIC_VIC_PRICES = {
    "fuelPriceDetails": [
        {
            "fuelStation": {
                "id": "s1", "name": "EG Ampol North Melbourne",
                "address": "250 Dryburg Street, North Melbourne, 3051",
                "brandId": "brand_eg_ampol",
                "location": {"latitude": -37.8005, "longitude": 144.9444},
            },
            "fuelPrices": [
                {"fuelType": "U91", "isAvailable": True, "price": 171.9, "updatedAt": "2026-06-17T05:00:00Z"},
                {"fuelType": "P95", "isAvailable": True, "price": 189.9, "updatedAt": "2026-06-17T05:00:00Z"},
            ],
            "updatedAt": "2026-06-17T05:00:00Z",
        },
        {
            "fuelStation": {
                "id": "s2", "name": "United Kensington",
                "address": "55 Epsom Rd, Kensington, 3031",
                "brandId": "brand_united",
                "location": {"latitude": -37.7935, "longitude": 144.9292},
            },
            "fuelPrices": [
                {"fuelType": "U91", "isAvailable": True, "price": 159.5, "updatedAt": "2026-06-17T05:00:00Z"},
                {"fuelType": "P98", "isAvailable": True, "price": 182.5, "updatedAt": "2026-06-17T05:00:00Z"},
                {"fuelType": "DSL", "isAvailable": True, "price": 189.9, "updatedAt": "2026-06-17T05:00:00Z"},
            ],
            "updatedAt": "2026-06-17T05:00:00Z",
        },
        {
            "fuelStation": {
                "id": "s3", "name": "Liberty Flemington Road",
                "address": "Flemington Rd, North Melbourne, 3051",
                "brandId": "brand_liberty",
                "location": {"latitude": -37.7963, "longitude": 144.9517},
            },
            "fuelPrices": [
                {"fuelType": "U91", "isAvailable": True, "price": 164.9, "updatedAt": "2026-06-17T05:00:00Z"},
                {"fuelType": "DSL", "isAvailable": True, "price": 203.9, "updatedAt": "2026-06-17T05:00:00Z"},
            ],
            "updatedAt": "2026-06-17T05:00:00Z",
        },
        {
            "fuelStation": {
                "id": "s4", "name": "BP Clarendon",
                "address": "Clarendon St, South Melbourne, 3205",
                "brandId": "brand_bp",
                "location": {"latitude": -37.8300, "longitude": 144.9580},
            },
            "fuelPrices": [
                {"fuelType": "U91", "isAvailable": True, "price": 162.9, "updatedAt": "2026-06-17T05:00:00Z"},
                {"fuelType": "P95", "isAvailable": True, "price": 179.9, "updatedAt": "2026-06-17T05:00:00Z"},
                {"fuelType": "P98", "isAvailable": True, "price": 187.9, "updatedAt": "2026-06-17T05:00:00Z"},
            ],
            "updatedAt": "2026-06-17T05:00:00Z",
        },
    ]
}

REALISTIC_VIC_BRANDS = {
    "brands": [
        {"id": "brand_eg_ampol", "name": "EG Ampol"},
        {"id": "brand_united", "name": "United"},
        {"id": "brand_liberty", "name": "Liberty"},
        {"id": "brand_bp", "name": "BP"},
    ]
}


def _mock_vic_cache(monkeypatch):
    """Inject realistic Melbourne data into the VIC cache."""
    def mock_fetch_json(url, consumer_id):
        if "prices" in url:
            return REALISTIC_VIC_PRICES
        if "brands" in url:
            return REALISTIC_VIC_BRANDS
        return {}

    monkeypatch.setattr("api.servo._fetch_vic_json", mock_fetch_json)
    monkeypatch.setattr("api.servo.VIC_CONSUMER_ID", "test-key")
    monkeypatch.setattr("api.servo._vic_cache", {"prices": None, "brands": None, "ts": 0})


def test_vic_route_returns_stations(monkeypatch):
    _mock_vic_cache(monkeypatch)
    stations = get_stations(MELBOURNE_ROUTE, fuel="91")
    assert stations is not None
    assert len(stations) >= 3
    for s in stations:
        assert s["lat"] is not None
        assert s["price"] > 0
        assert s["brand"] in ("EG Ampol", "United", "Liberty", "BP")


def test_vic_route_picks_cheapest_stop(monkeypatch):
    _mock_vic_cache(monkeypatch)
    stations = get_stations(MELBOURNE_ROUTE, fuel="91")
    result = find_best_station(
        MELBOURNE_ROUTE, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=3.4,
        has_rac=False, has_woolies=False,
        region="VIC",
    )
    assert result["status"] == "ok"
    st = result["station"]
    assert st["price"] > 0
    assert st["diversion_km"] >= 0
    assert st["cost_cents_min"] > 0
    assert st["cost_cents_max"] >= st["cost_cents_min"]


def test_vic_route_no_stop_full_tank(monkeypatch):
    _mock_vic_cache(monkeypatch)
    stations = get_stations(MELBOURNE_ROUTE, fuel="91")
    result = find_best_station(
        MELBOURNE_ROUTE, stations,
        efficiency_l_per_100km=10.0,
        capacity_litres=50.0,
        current_litres=45.0,
        has_rac=False, has_woolies=False,
        region="VIC",
    )
    assert result["status"] == "no_stop_needed"


def test_vic_route_racv_discount_applied(monkeypatch):
    _mock_vic_cache(monkeypatch)
    stations = get_stations(MELBOURNE_ROUTE, fuel="91")

    result_no_disc = find_best_station(
        MELBOURNE_ROUTE, stations, 10.0, 50.0, 3.4,
        has_rac=False, has_woolies=False, region="VIC",
    )
    result_racv = find_best_station(
        MELBOURNE_ROUTE, stations, 10.0, 50.0, 3.4,
        has_rac=True, has_woolies=False, region="VIC",
    )
    assert result_no_disc["status"] == "ok"
    assert result_racv["status"] == "ok"
    # With RACV, EG Ampol gets 5c/L off — cost should be lower or station should change
    assert result_racv["station"]["cost_cents_min"] <= result_no_disc["station"]["cost_cents_min"]


def test_vic_route_diesel_fuel_type(monkeypatch):
    _mock_vic_cache(monkeypatch)
    stations = get_stations(MELBOURNE_ROUTE, fuel="DIESEL")
    assert stations is not None
    assert len(stations) >= 1
    for s in stations:
        assert s["price"] > 0


def test_vic_route_detects_region_automatically(monkeypatch):
    _mock_vic_cache(monkeypatch)
    # Don't pass region explicitly — it should auto-detect VIC from the route
    stations = get_stations(MELBOURNE_ROUTE, fuel="91")
    assert stations is not None
    assert len(stations) > 0


def test_wa_route_not_affected_by_vic_cache(monkeypatch):
    """A Perth route should still use FuelWatch, not the VIC cache."""
    _mock_vic_cache(monkeypatch)
    perth_route = [[-31.95, 115.86], [-31.98, 115.90]]
    # get_stations for WA calls fetch_stations_wa which hits the real FuelWatch API.
    # We don't want to hit the network in tests, so just verify the region detection
    # routes it to WA, not VIC.
    mid = perth_route[len(perth_route) // 2]
    assert detect_state(mid[0], mid[1]) == "WA"


# -- VIC cached fetch ---------------------------------------------------------


def test_fetch_stations_vic_cached_returns_stations(monkeypatch):
    """Inject fake API responses so no real network call is made."""
    fake_prices = SAMPLE_VIC_RESPONSE
    fake_brands = {"brands": [
        {"id": "brand_shell", "name": "Shell"},
        {"id": "brand_bp", "name": "BP"},
    ]}

    def mock_fetch_json(url, consumer_id):
        if "prices" in url:
            return fake_prices
        if "brands" in url:
            return fake_brands
        return {}

    monkeypatch.setattr("api.servo._fetch_vic_json", mock_fetch_json)
    monkeypatch.setattr("api.servo.VIC_CONSUMER_ID", "test-key")
    monkeypatch.setattr("api.servo._vic_cache", {"prices": None, "brands": None, "ts": 0})

    stations = fetch_stations_vic_cached(fuel="91")
    assert len(stations) == 1
    assert stations[0]["brand"] == "Shell"
    assert stations[0]["price"] == 163.9
