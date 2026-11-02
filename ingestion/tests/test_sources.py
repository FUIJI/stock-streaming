import pytest

from src.schemas import Trade
from src.sources import SimulatorSource, build_source, parse_alpaca_message


async def take(source, n: int):
    trades = []
    async for trade in source.stream():
        trades.append(trade)
        if len(trades) >= n:
            break
    return trades


async def test_simulator_produces_valid_trades():
    symbols = ["AAPL", "MSFT"]
    source = SimulatorSource(symbols, trades_per_sec=1000.0, seed=42)
    trades = await take(source, 50)

    assert {t.symbol for t in trades} == set(symbols)
    assert all(t.price > 0 for t in trades)
    assert all(t.volume >= 0 for t in trades)
    # Event time must never go backwards within the stream.
    ts_values = [t.ts for t in trades]
    assert ts_values == sorted(ts_values)


async def test_simulator_round_robin_covers_all_symbols():
    symbols = ["A", "B", "C"]
    source = SimulatorSource(symbols, trades_per_sec=1000.0, seed=1)
    trades = await take(source, 9)
    assert [t.symbol for t in trades] == symbols * 3


def test_simulator_requires_symbols():
    with pytest.raises(ValueError):
        SimulatorSource([], trades_per_sec=5.0)


def test_build_source_rejects_unknown():
    with pytest.raises(ValueError):
        build_source("carrier-pigeon", ["AAPL"], 5.0, "")


def test_build_source_finnhub_requires_key():
    with pytest.raises(ValueError):
        build_source("finnhub", ["AAPL"], 5.0, "")


def test_build_source_alpaca_requires_key_and_secret():
    with pytest.raises(ValueError):
        build_source("alpaca", ["AAPL"], 5.0, "", alpaca_api_key="k", alpaca_api_secret="")


def test_parse_alpaca_trade_keeps_millisecond_event_time():
    trade = parse_alpaca_message(
        {"T": "t", "S": "AAPL", "p": 126.55, "s": 100, "t": "2021-02-22T15:51:44.208123456Z"}
    )
    assert trade == Trade(symbol="AAPL", price=126.55, volume=100.0, ts=1614009104208)


def test_parse_alpaca_trade_without_fraction():
    trade = parse_alpaca_message(
        {"T": "t", "S": "AAPL", "p": 1.0, "s": 1, "t": "2021-02-22T15:51:44Z"}
    )
    assert trade.ts == 1614009104000


@pytest.mark.parametrize(
    "msg",
    [
        {"T": "success", "msg": "authenticated"},
        {"T": "subscription", "trades": ["AAPL"]},
        {"T": "q", "S": "AAPL"},
    ],
)
def test_parse_alpaca_ignores_control_and_quote_messages(msg):
    assert parse_alpaca_message(msg) is None


def test_parse_alpaca_error_raises():
    with pytest.raises(RuntimeError, match="402"):
        parse_alpaca_message({"T": "error", "code": 402, "msg": "auth failed"})
