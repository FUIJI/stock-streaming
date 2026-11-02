from datetime import UTC, datetime

from src.backfill import parse_bars


def test_parse_bars_maps_alpaca_payload_to_candle_rows():
    payload = {
        "bars": {
            "AAPL": [
                {"t": "2026-10-28T13:30:00Z", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5,
                 "v": 300, "n": 12, "vw": 1.2},
            ],
            "MSFT": [],
        },
        "next_page_token": None,
    }
    assert parse_bars(payload) == [
        ("AAPL", datetime(2026, 10, 28, 13, 30, tzinfo=UTC), 1.0, 2.0, 0.5, 1.5, 300.0, 12),
    ]


def test_parse_bars_handles_empty_response():
    assert parse_bars({"bars": None}) == []
