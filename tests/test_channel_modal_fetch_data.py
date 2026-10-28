from src.ui.views.channels_view import FETCH_DATA_OPTIONS, parse_fetch_limit


def test_fetch_data_options_has_fetch_no_data_at_top():
    assert len(FETCH_DATA_OPTIONS) >= 7
    assert FETCH_DATA_OPTIONS[0] == "Fetch No Data"
    assert "50" in FETCH_DATA_OPTIONS
    assert "100" in FETCH_DATA_OPTIONS
    assert "200" in FETCH_DATA_OPTIONS
    assert "300" in FETCH_DATA_OPTIONS
    assert "500" in FETCH_DATA_OPTIONS
    assert "All" in FETCH_DATA_OPTIONS


def test_parse_fetch_limit():
    # Top option: Fetch No Data
    assert parse_fetch_limit("Fetch No Data") is None
    assert parse_fetch_limit("No Fetching") is None
    assert parse_fetch_limit("0 (Skip)") is None
    assert parse_fetch_limit("skip") is None
    assert parse_fetch_limit("none") is None

    # Step increments
    assert parse_fetch_limit("50") == 50
    assert parse_fetch_limit("100") == 100
    assert parse_fetch_limit("200") == 200
    assert parse_fetch_limit("300") == 300
    assert parse_fetch_limit("500") == 500

    # All option (0 indicates fetch all until playlist exhausted)
    assert parse_fetch_limit("All") == 0
    assert parse_fetch_limit("all") == 0

    # Default fallback
    assert parse_fetch_limit("invalid") == 100
    assert parse_fetch_limit(None) is None
