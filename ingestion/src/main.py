import asyncio
import logging

from .config import Settings
from .sinks import build_sink
from .sources import build_source

log = logging.getLogger("ingestion")


async def run() -> None:
    settings = Settings.from_env()
    log.info(
        "Starting ingestion: source=%s sink=%s symbols=%s",
        settings.trade_source, settings.trade_sink, settings.symbols,
    )
    source = build_source(
        settings.trade_source, settings.symbols,
        settings.sim_trades_per_sec, settings.finnhub_api_key,
        settings.alpaca_api_key, settings.alpaca_api_secret, settings.alpaca_feed,
    )
    sink = build_sink(
        settings.trade_sink, settings.kafka_broker, settings.trades_topic,
        settings.api_ingest_url, settings.ingest_token,
    )
    await sink.consume(source.stream())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
