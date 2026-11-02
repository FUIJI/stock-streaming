import logging
from collections.abc import AsyncIterator
from typing import Protocol

from .schemas import Trade

log = logging.getLogger(__name__)

LOG_EVERY = 500


class TradeSink(Protocol):
    async def consume(self, trades: AsyncIterator[Trade]) -> None: ...


def _log_progress(produced: int, trade: Trade) -> None:
    if produced % LOG_EVERY == 0:
        log.info("Produced %d trades (last: %s @ %s)", produced, trade.symbol, trade.price)


class KafkaSink:
    """Full stack: trades -> Redpanda topic, keyed by symbol."""

    def __init__(self, broker: str, topic: str):
        self._broker = broker
        self._topic = topic

    def _ensure_topic(self, partitions: int = 3) -> None:
        from confluent_kafka import KafkaException
        from confluent_kafka.admin import AdminClient, NewTopic

        admin = AdminClient({"bootstrap.servers": self._broker})
        if self._topic in admin.list_topics(timeout=10).topics:
            return
        futures = admin.create_topics([NewTopic(self._topic, num_partitions=partitions)])
        try:
            futures[self._topic].result(timeout=10)
            log.info("Created topic %s (%d partitions)", self._topic, partitions)
        except KafkaException as exc:
            # Racing another instance is fine; anything else is not.
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise

    async def consume(self, trades: AsyncIterator[Trade]) -> None:
        from confluent_kafka import Producer

        self._ensure_topic()
        producer = Producer({"bootstrap.servers": self._broker, "linger.ms": 50, "acks": "all"})

        def on_delivery(err, msg) -> None:
            if err is not None:
                log.error("Delivery failed for key=%s: %s", msg.key(), err)

        produced = 0
        try:
            async for trade in trades:
                producer.produce(
                    self._topic,
                    key=trade.symbol.encode(),
                    value=trade.model_dump_json().encode(),
                    on_delivery=on_delivery,
                )
                producer.poll(0)
                produced += 1
                _log_progress(produced, trade)
        finally:
            log.info("Flushing producer (%d trades produced)", produced)
            producer.flush(10)


class ApiSink:
    """Lite mode: trades -> the API's internal ingest WebSocket (no broker in between).

    Trades are sent as the same JSON the Kafka sink produces. While the API is
    unreachable the source is simply not read, so nothing piles up in memory.
    """

    def __init__(self, url: str, token: str):
        if not token:
            raise ValueError("TRADE_SINK=api requires INGEST_TOKEN")
        self._url = url
        self._token = token

    async def consume(self, trades: AsyncIterator[Trade]) -> None:
        import websockets

        produced = 0
        headers = {"Authorization": f"Bearer {self._token}"}
        # `async for ws in connect(...)` reconnects forever with backoff.
        async for ws in websockets.connect(self._url, additional_headers=headers):
            log.info("Connected to API ingest socket %s", self._url)
            try:
                async for trade in trades:
                    await ws.send(trade.model_dump_json())
                    produced += 1
                    _log_progress(produced, trade)
                return
            except websockets.ConnectionClosed:
                log.warning("API ingest socket closed, reconnecting")
                continue


def build_sink(trade_sink: str, kafka_broker: str, trades_topic: str,
               api_ingest_url: str, ingest_token: str) -> TradeSink:
    if trade_sink == "kafka":
        return KafkaSink(kafka_broker, trades_topic)
    if trade_sink == "api":
        return ApiSink(api_ingest_url, ingest_token)
    raise ValueError(f"Unknown TRADE_SINK: {trade_sink!r} (expected kafka|api)")
