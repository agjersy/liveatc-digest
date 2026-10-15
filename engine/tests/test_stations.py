from liveatc_digest.sources.liveatc import Role, fetch_stations, infer_role, parse_stations


def test_parse_stations(search_html):
    stations = parse_stations(search_html, "kbed")
    assert [s.identifier for s in stations] == ["kbed_twr", "kbed_app"]
    twr, app = stations
    assert twr.icao == "KBED"
    assert twr.title == "KBED Tower"
    assert twr.up is True
    assert [(f.title, f.mhz) for f in twr.frequencies] == [
        ("Hanscom Tower", "118.500"),
        ("Hanscom Ground", "121.700"),
    ]
    assert app.up is False
    assert app.role is Role.APPROACH


def test_infer_role():
    assert infer_role("kbed_twr") is Role.TOWER
    assert infer_role("kbed_gnd") is Role.GROUND
    assert infer_role("kbed_del", "Clearance Delivery") is Role.CLEARANCE
    assert infer_role("zbw_ctr", "Boston Center") is Role.CENTER
    assert infer_role("kxyz", "Something") is Role.UNKNOWN


def test_fetch_stations_uses_search_url(client, session, search_html):
    from tests.conftest import FakeResponse

    session.add(
        "https://www.liveatc.net/search/?icao=kbed", FakeResponse(200, search_html.encode())
    )
    stations = fetch_stations(client, "KBED")
    assert len(stations) == 2
    assert session.calls == ["https://www.liveatc.net/search/?icao=kbed"]
