from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def get_market(request: Request):
    market = getattr(request.app.state, "market", None)
    if market is None:
        raise HTTPException(status_code=503, detail="Market calendar needs ALPACA_API_KEY/SECRET")
    return market


@router.get("/market/status")
async def market_status(request: Request) -> dict:
    """Current US equity session: pre (04:00-09:30 ET), regular, post (16:00-20:00 ET), closed."""
    try:
        return await get_market(request).status()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Market calendar unavailable") from exc
