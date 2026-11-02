from datetime import datetime

from pydantic import BaseModel


class Candle(BaseModel):
    symbol: str
    bucket_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int


class SymbolInfo(BaseModel):
    symbol: str
    name: str | None
    exchange: str | None
